"""Асинхронный клиент Sber Звук (zvuk.com) на httpx.

Публичный анонимный API веб-плеера Звука. Токен добывается лениво через
`/api/tiny/profile` (анонимный, привязан к cookie), затем передаётся в
GraphQL-запросы заголовком `X-Auth-Token`. Anti-bot защита (307-редирект +
cookie `spid`) обходится общим cookie-jar внутри `httpx.AsyncClient` и
браузерным User-Agent.

Клиент standalone — НЕ импортирует Home Assistant. Используется для обогащения
now-playing метаданными и для сборки staros:// deeplink'ов из zvuk.com-ссылок.
"""
from __future__ import annotations

import asyncio
import colorsys
import io
import logging
import re
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

PROFILE_URL = "https://zvuk.com/api/tiny/profile"
GRAPHQL_URL = "https://zvuk.com/api/v1/graphql"
SEARCH_URL = "https://zvuk.com/api/tiny/search"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 10.0
_COVER_SIZE = "400x400"

# getTracks — ПРОВЕРЕНО вживую.
_GET_TRACKS_QUERY = (
    "query getTracks($ids:[ID!]!){getTracks(ids:$ids){"
    "id title duration explicit hasFlac lyrics "
    "artists{id title} release{id title image{src}}}}"
)
# Обогащение обложками результатов поиска — ПРОВЕРЕНО вживую.
_GET_ARTISTS_QUERY = (
    "query getArtists($ids:[ID!]!){getArtists(ids:$ids){id image{src}}}"
)
_GET_RELEASES_QUERY = (
    "query getReleases($ids:[ID!]!){getReleases(ids:$ids){id image{src}}}"
)
# Drill-down: детали артиста (релизы + топ-треки) и релиза (треклист).
# ПРОВЕРЕНО вживую.
_GET_ARTIST_QUERY = (
    "query getArtist($ids:[ID!]!){getArtists(ids:$ids){"
    "id title image{src} "
    "releases{id title date type image{src}} "
    "popularTracks(limit:20){id title duration explicit "
    "artists{title} release{image{src}}}}}"
)
_GET_RELEASE_QUERY = (
    "query getRelease($ids:[ID!]!){getReleases(ids:$ids){"
    "id title date type image{src} artists{id title} "
    "tracks{id title duration explicit hasFlac artists{title}}}}"
)

# Категории поиска: doc_type Звука → (ключ ответа, pt для deeplink).
# release == альбом. Порядок задаёт секции в панели.
_SEARCH_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("artist", "artists", "artist"),
    ("release", "releases", "release"),
    ("track", "tracks", "track"),
    ("playlist", "playlists", "playlist"),
)

# zvuk.com/{kind}/<id> → (pt, id_param). id_param: 'tid' для треков/подкастов,
# 'pid' для сущностей-контейнеров (артист/релиз/плейлист).
_URL_KIND_MAP: dict[str, tuple[str, str]] = {
    "track": ("track", "tid"),
    "artist": ("artist", "pid"),
    "release": ("release", "pid"),  # TODO: pt=release для альбома не подтверждён
    "playlist": ("playlist", "pid"),
    "abook": ("podcast", "tid"),
}
_URL_RE = re.compile(
    r"zvuk\.com/(track|artist|release|playlist|abook)/(\d+)", re.IGNORECASE
)


def _dominant_color_from_bytes(data: bytes) -> str | None:
    """Доминирующий «живой» цвет картинки → hex. Выполняется в executor.

    Взвешенное по насыщенности среднее (тянем к цветному, а не к серому),
    затем поднимаем насыщенность и держим светлоту в приятном для glow
    диапазоне. Pillow импортируется лениво — модуль остаётся импортируемым
    без Pillow (нужен только для этого пути).
    """
    from PIL import Image  # ленивый импорт: Pillow нужен только здесь

    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((48, 48))
    px = im.load()
    w, h = im.size
    r = g = b = wsum = 0.0
    for y in range(h):
        for x in range(w):
            rr, gg, bb = px[x, y]
            mx, mn = max(rr, gg, bb), min(rr, gg, bb)
            sat = 0.0 if mx == 0 else (mx - mn) / mx
            weight = 0.15 + sat * sat  # приоритет насыщенным пикселям
            r += rr * weight
            g += gg * weight
            b += bb * weight
            wsum += weight
    if wsum == 0:
        return None
    r, g, b = r / wsum, g / wsum, b / wsum
    hh, ll, ss = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    ss = min(1.0, ss * 1.35 + 0.1)
    ll = min(0.68, max(0.45, ll))
    rr, gg, bb = colorsys.hls_to_rgb(hh, ll, ss)
    return f"#{round(rr * 255):02x}{round(gg * 255):02x}{round(bb * 255):02x}"


class ZvukClient:
    """Клиент публичного API Sber Звук (анонимный токен + GraphQL)."""

    def __init__(self) -> None:
        """Создать клиент. Сетевые ресурсы поднимаются лениво."""
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._color_cache: dict[str, str | None] = {}

    def _http(self) -> httpx.AsyncClient:
        """Лениво создать общий httpx-клиент с cookie-jar и браузерным UA."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers={"User-Agent": _BROWSER_UA},
            )
        return self._client

    async def aclose(self) -> None:
        """Закрыть httpx-клиент (освободить соединения)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_token(self, *, force: bool = False) -> str:
        """Вернуть анонимный токен, кешируя его в инстансе.

        `force=True` — принудительно обновить (например, после 401).
        """
        if self._token and not force:
            return self._token
        resp = await self._http().get(PROFILE_URL)
        resp.raise_for_status()
        data = resp.json()
        token = (data.get("result") or {}).get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("Звук: токен не найден в /profile")
        self._token = token
        return token

    async def _graphql(
        self, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Выполнить GraphQL-запрос с X-Auth-Token. Вернуть блок `data`."""
        token = await self.get_token()
        payload = {"query": query, "variables": variables}
        resp = await self._http().post(
            GRAPHQL_URL, json=payload, headers={"X-Auth-Token": token}
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            _LOGGER.debug("Звук GraphQL errors: %s", body["errors"])
        return body.get("data") or {}

    async def get_tracks(self, ids: list[str]) -> list[dict[str, Any]]:
        """Метаданные треков по каталожным id. Вернуть нормализованные dict'ы."""
        if not ids:
            return []
        data = await self._graphql(_GET_TRACKS_QUERY, {"ids": [str(i) for i in ids]})
        raw = data.get("getTracks") or []
        return [self._normalize_track(t) for t in raw if isinstance(t, dict)]

    @staticmethod
    def _normalize_track(t: dict[str, Any]) -> dict[str, Any]:
        """Привести getTracks-элемент к плоскому dict со стабильными ключами."""
        release = t.get("release") or {}
        image = release.get("image") or {}
        cover_src = image.get("src")
        cover_url = (
            cover_src.replace("{size}", _COVER_SIZE)
            if isinstance(cover_src, str) and cover_src
            else None
        )
        artists = [
            {"id": a.get("id"), "title": a.get("title")}
            for a in (t.get("artists") or [])
            if isinstance(a, dict)
        ]
        lyrics = t.get("lyrics")
        return {
            "id": t.get("id"),
            "title": t.get("title"),
            "artists": artists,
            "album": {"id": release.get("id"), "title": release.get("title")},
            "cover_url": cover_url,
            "duration": t.get("duration"),
            "explicit": bool(t.get("explicit")),
            "has_flac": bool(t.get("hasFlac")),
            "has_lyrics": bool(lyrics),
        }

    @staticmethod
    def _year(date_str: Any) -> int | None:
        """ISO-дата → год (int) или None."""
        if isinstance(date_str, str) and len(date_str) >= 4 and date_str[:4].isdigit():
            return int(date_str[:4])
        return None

    def _cover(self, src: Any) -> str | None:
        """image.src (с плейсхолдером {size}) → готовый URL обложки."""
        return (
            src.replace("{size}", _COVER_SIZE)
            if isinstance(src, str) and src
            else None
        )

    def _flat_track(self, t: dict[str, Any], cover: str | None) -> dict[str, Any]:
        """Элемент трека (popularTracks/release.tracks) → плоский dict панели."""
        rel = t.get("release") or {}
        rel_cover = cover if cover is not None else self._cover(
            (rel.get("image") or {}).get("src")
        )
        return {
            "id": str(t.get("id")),
            "type": "track",
            "title": t.get("title") or "",
            "artists": [
                a.get("title")
                for a in (t.get("artists") or [])
                if isinstance(a, dict) and a.get("title")
            ],
            "duration": t.get("duration"),
            "explicit": bool(t.get("explicit")),
            "has_flac": bool(t.get("hasFlac")),
            "cover_url": rel_cover,
            "pt": "track",
        }

    async def get_artist(self, artist_id: str) -> dict[str, Any] | None:
        """Детали артиста для drill-down: релизы (новые сверху) + топ-треки."""
        if not artist_id:
            return None
        try:
            data = await self._graphql(_GET_ARTIST_QUERY, {"ids": [str(artist_id)]})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук get_artist(%s) не удался: %s", artist_id, exc)
            return None
        rows = data.get("getArtists") or []
        if not rows or not isinstance(rows[0], dict):
            return None
        a = rows[0]
        releases = []
        for r in a.get("releases") or []:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            year = self._year(r.get("date"))
            rtype = r.get("type") or "album"
            label = "Сингл" if rtype == "single" else "Альбом"
            releases.append({
                "id": str(r["id"]),
                "type": "release",
                "title": r.get("title") or "",
                "subtitle": f"{label} · {year}" if year else label,
                "year": year,
                "release_type": rtype,
                "cover_url": self._cover((r.get("image") or {}).get("src")),
                "pt": "release",
            })
        tracks = [
            self._flat_track(t, None)
            for t in (a.get("popularTracks") or [])
            if isinstance(t, dict) and t.get("id")
        ]
        return {
            "id": str(a.get("id")),
            "type": "artist",
            "title": a.get("title") or "",
            "cover_url": self._cover((a.get("image") or {}).get("src")),
            "releases": releases,
            "tracks": tracks,
        }

    async def get_release(self, release_id: str) -> dict[str, Any] | None:
        """Детали релиза для drill-down: шапка + полный треклист."""
        if not release_id:
            return None
        try:
            data = await self._graphql(
                _GET_RELEASE_QUERY, {"ids": [str(release_id)]}
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук get_release(%s) не удался: %s", release_id, exc)
            return None
        rows = data.get("getReleases") or []
        if not rows or not isinstance(rows[0], dict):
            return None
        r = rows[0]
        cover = self._cover((r.get("image") or {}).get("src"))
        artists = [
            {"id": str(a.get("id")), "title": a.get("title") or ""}
            for a in (r.get("artists") or [])
            if isinstance(a, dict)
        ]
        tracks = [
            self._flat_track(t, cover)
            for t in (r.get("tracks") or [])
            if isinstance(t, dict) and t.get("id")
        ]
        year = self._year(r.get("date"))
        return {
            "id": str(r.get("id")),
            "type": "release",
            "title": r.get("title") or "",
            "year": year,
            "release_type": r.get("type") or "album",
            "cover_url": cover,
            "artists": artists,
            "artist": ", ".join(a["title"] for a in artists if a["title"]),
            "tracks": tracks,
        }

    async def dominant_cover_color(self, url: str) -> str | None:
        """Доминирующий цвет обложки (hex) для ambient-glow панели.

        Считается на сервере (Pillow), т.к. CDN Звука не отдаёт CORS-заголовки
        и клиентский canvas-разбор невозможен. Кешируется по URL. Тяжёлый
        Pillow-разбор уходит в executor, чтобы не блокировать event loop.
        None — если картинку не удалось получить/разобрать.
        """
        if not url:
            return None
        if url in self._color_cache:
            return self._color_cache[url]
        try:
            resp = await self._http().get(url)
            resp.raise_for_status()
            data = resp.content
            color = await asyncio.get_running_loop().run_in_executor(
                None, _dominant_color_from_bytes, data
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            _LOGGER.debug("Звук: не удалось извлечь цвет обложки %s: %s", url, exc)
            color = None
        self._color_cache[url] = color
        return color

    async def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        """Поиск по каталогу Звука — ПРОВЕРЕНО вживую (REST /api/tiny/search).

        Возвращает категоризированный результат для панели::

            {
              "best": {"type": "track", "id": "84279911"} | None,
              "artists":   [item, ...],
              "releases":  [item, ...],   # альбомы
              "tracks":    [item, ...],
              "playlists": [item, ...],
            }

        item = {id, type, title, subtitle, cover_url, pt, explicit, duration}.
        Обложки добираются batch-запросами getArtists/getReleases/getTracks
        (best-effort — при ошибке просто без обложки).
        """
        if not query.strip():
            return self._empty_search()
        types = ",".join(cat[0] for cat in _SEARCH_CATEGORIES)
        token = await self.get_token()
        try:
            resp = await self._http().get(
                SEARCH_URL,
                params={"query": query, "type": types, "limit": limit},
                headers={"X-Auth-Token": token},
            )
            resp.raise_for_status()
            search = ((resp.json() or {}).get("result") or {}).get("search") or {}
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук search не удался для %r: %s", query, exc)
            return self._empty_search()

        out: dict[str, Any] = self._empty_search()
        best = search.get("best_item") or {}
        if isinstance(best, dict) and best.get("id") and best.get("doc_type"):
            out["best"] = {"type": best["doc_type"], "id": str(best["id"])}

        for doc_type, key, pt in _SEARCH_CATEGORIES:
            raw = ((search.get(key) or {}).get("items")) or []
            out[key] = [
                self._search_item(it, doc_type, pt)
                for it in raw[:limit]
                if isinstance(it, dict) and it.get("id")
            ]

        await self._enrich_search_covers(out)
        return out

    @staticmethod
    def _empty_search() -> dict[str, Any]:
        """Пустой каркас результата поиска (все категории — списки)."""
        return {"best": None, "artists": [], "releases": [],
                "tracks": [], "playlists": []}

    @staticmethod
    def _search_item(it: dict[str, Any], doc_type: str, pt: str) -> dict[str, Any]:
        """Элемент /api/tiny/search → плоский item для панели (без обложки)."""
        aname = it.get("aname")
        if doc_type == "artist":
            subtitle = "Исполнитель"
        elif doc_type == "playlist":
            n = it.get("tracks_number")
            subtitle = f"Плейлист · {n} треков" if n else "Плейлист"
        elif doc_type == "release":
            subtitle = f"{aname} · Альбом" if aname else "Альбом"
        else:  # track
            subtitle = aname or ""
        return {
            "id": str(it["id"]),
            "type": doc_type,
            "title": it.get("title") or "",
            "subtitle": subtitle,
            "artist": aname,
            "cover_url": None,
            "pt": pt,
            "explicit": bool(it.get("explicit")),
            "duration": it.get("duration"),
        }

    async def _enrich_search_covers(self, out: dict[str, Any]) -> None:
        """Добрать обложки для artists/releases/tracks одним заходом (concurrent)."""
        async def enrich(key: str, query: str, gql_key: str) -> None:
            items = out.get(key) or []
            ids = [it["id"] for it in items]
            if not ids:
                return
            try:
                data = await self._graphql(query, {"ids": ids})
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                _LOGGER.debug("Звук обогащение %s не удалось: %s", key, exc)
                return
            covers: dict[str, str] = {}
            for row in data.get(gql_key) or []:
                if not isinstance(row, dict):
                    continue
                src = (row.get("image") or {}).get("src")
                if isinstance(src, str) and src:
                    covers[str(row.get("id"))] = src.replace("{size}", _COVER_SIZE)
            for it in items:
                it["cover_url"] = covers.get(it["id"])

        await asyncio.gather(
            enrich("artists", _GET_ARTISTS_QUERY, "getArtists"),
            enrich("releases", _GET_RELEASES_QUERY, "getReleases"),
            self._enrich_track_covers(out.get("tracks") or []),
        )

    async def _enrich_track_covers(self, tracks: list[dict[str, Any]]) -> None:
        """Обложки треков через getTracks (обложка = обложка релиза)."""
        ids = [it["id"] for it in tracks]
        if not ids:
            return
        try:
            metas = await self.get_tracks(ids)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук обогащение tracks не удалось: %s", exc)
            return
        covers = {str(m.get("id")): m.get("cover_url") for m in metas}
        for it in tracks:
            it["cover_url"] = covers.get(it["id"])

    async def search_first_deeplink(self, query: str) -> str | None:
        """Поиск по названию → deeplink первого результата (best → track → …).

        Для сервиса play_music с текстовым запросом. None — если пусто.
        """
        result = await self.search(query, limit=1)
        best = result.get("best")
        if isinstance(best, dict) and best.get("id"):
            for doc_type, _key, pt in _SEARCH_CATEGORIES:
                if doc_type == best["type"]:
                    id_param = "tid" if pt in ("track", "podcast") else "pid"
                    return self.build_deeplink(pt, id_param, best["id"])
        for _doc_type, key, pt in _SEARCH_CATEGORIES:
            items = result.get(key) or []
            if items:
                id_param = "tid" if pt in ("track", "podcast") else "pid"
                return self.build_deeplink(pt, id_param, items[0]["id"])
        return None

    @staticmethod
    def parse_zvuk_url(
        url_or_id: str, kind: str | None = None
    ) -> tuple[str, str, str] | None:
        """Разобрать zvuk.com-ссылку ИЛИ голый id+kind.

        Возвращает `(pt, id_param, value)` для сборки deeplink, где
        `id_param` ∈ {'tid','pid'}. None — если разобрать не удалось.
        """
        text = (url_or_id or "").strip()
        match = _URL_RE.search(text)
        if match:
            url_kind = match.group(1).lower()
            value = match.group(2)
            pt, id_param = _URL_KIND_MAP[url_kind]
            return pt, id_param, value
        # Голый id + явный kind.
        if kind and text.isdigit():
            mapping = _URL_KIND_MAP.get(kind.lower())
            if mapping:
                pt, id_param = mapping
                return pt, id_param, text
        return None

    @staticmethod
    def build_deeplink(pt: str, id_param: str, value: str) -> str:
        """Собрать staros://music deeplink из (pt, id_param, value)."""
        return f"staros://music?{id_param}={value}&pt={pt}"
