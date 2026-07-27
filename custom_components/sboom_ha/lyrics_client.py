"""Получение текстов песен из Lrclib.net (open API, без auth)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "sboom_ha/HomeAssistant"

# NetEase Cloud Music — резервный источник synced-текстов (публичные
# JSON-endpoints без авторизации). Порядок цепочки: LRCLIB → NetEase.
NETEASE_SEARCH_URL = "https://music.163.com/api/search/get"
NETEASE_LYRIC_URL = "https://music.163.com/api/song/lyric"
_NETEASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://music.163.com/",
}
# Допустимое расхождение длительности при матчинге трека в выдаче поиска.
_NETEASE_DURATION_TOLERANCE_SEC = 7
# Служебные строки-кредиты в LRC от NetEase ([00:00.00] 作曲 : Имя) — не текст
# песни: без фильтра «作曲 : Егор Летов» висел бы в караоке до первой строки.
_NETEASE_CREDIT_RE = re.compile(
    r"^\s*(作曲|作词|编曲|制作人|出品|混音|母带|录音|吉他|贝斯|鼓|键盘)\s*[:：]"
)

# Таймстамп LRC: [MM:SS.cc] или [MM:SS.ccc]. Строка может начинаться с
# НЕСКОЛЬКИХ таймстампов подряд ([00:10.00][01:30.00]Припев) — одна строка
# текста на несколько моментов времени.
_LRC_TS = re.compile(r"\[(\d{1,2}):(\d{2})\.(\d{2,3})\]")
# Word-теги enhanced LRC внутри текста: <MM:SS.cc> — вычищаем.
_LRC_WORD_TAG = re.compile(r"<\d{1,2}:\d{2}\.\d{2,3}>")


@dataclass(slots=True)
class Lyrics:
    plain: str | None
    synced: str | None
    instrumental: bool
    source: str  # "lrclib"
    # Распарсенные timestamps (sec → text); None если sync недоступен
    timeline: list[tuple[float, str]] | None


def _parse_lrc(synced: str) -> list[tuple[float, str]]:
    """LRC → отсортированный по времени список (sec, text).

    Поддерживает multi-timestamp строки ([00:10.00][01:30.00]Припев — текст
    попадает в обе точки) и вычищает word-теги enhanced LRC (<00:12.34>).
    """
    out: list[tuple[float, str]] = []
    for line in synced.splitlines():
        stamps: list[re.Match[str]] = []
        end = 0
        for m in _LRC_TS.finditer(line):
            if m.start() != end:
                break  # таймстампы только подряд в начале строки
            stamps.append(m)
            end = m.end()
        if not stamps:
            continue
        text = _LRC_WORD_TAG.sub("", line[end:]).strip()
        for m in stamps:
            mm, ss, cc = m.groups()
            cs = int(cc) / (1000 if len(cc) == 3 else 100)
            out.append((int(mm) * 60 + int(ss) + cs, text))
    out.sort(key=lambda x: x[0])
    return out


def lyrics_to_dict(lyrics: Lyrics) -> dict:
    """Сериализация Lyrics для HA Store. timeline опускаем — derived из synced."""
    return {
        "plain": lyrics.plain,
        "synced": lyrics.synced,
        "instrumental": lyrics.instrumental,
        "source": lyrics.source,
    }


def lyrics_from_dict(data: dict) -> Lyrics:
    """Десериализация Lyrics из HA Store. timeline пересобирается из synced."""
    synced = data.get("synced")
    return Lyrics(
        plain=data.get("plain"),
        synced=synced,
        instrumental=bool(data.get("instrumental", False)),
        source=data.get("source") or "lrclib",
        timeline=_parse_lrc(synced) if synced else None,
    )


async def _request_get(
    session: aiohttp.ClientSession,
    track: str,
    artist: str,
    album: str | None,
    duration_sec: int | None,
    timeout: float,
) -> dict | None | str:
    """Один запрос. Возвращает dict (data), None (network err), или 'not_found'."""
    params: dict[str, str] = {"track_name": track, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration_sec:
        params["duration"] = str(int(duration_sec))
    try:
        async with session.get(
            f"{LRCLIB_BASE}/get",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            if r.status == 404:
                return "not_found"
            if r.status != 200:
                _LOGGER.debug("lrclib HTTP %s for %r — %r", r.status, track, artist)
                return None
            return await r.json()
    except (TimeoutError, aiohttp.ClientError) as exc:
        _LOGGER.debug("lrclib network err: %s", exc.__class__.__name__)
        return None


async def _fetch_lrclib(
    session: aiohttp.ClientSession,
    track: str,
    artist: str,
    album: str | None,
    duration_sec: int | None,
    timeout: float,
    retries: int,
) -> tuple[Lyrics | None, bool]:
    """Lrclib с retry. Возвращает (lyrics | None, была_ли_сетевая_ошибка)."""
    # Пробы: (a) полные параметры, (b) только track+artist (некоторые песни в lrclib
    # без album metadata).
    attempts: list[tuple[str | None, int | None]] = [(album, duration_sec)]
    if album or duration_sec:
        attempts.append((None, None))

    network_err = False
    for attempt_album, attempt_dur in attempts:
        for retry in range(retries + 1):
            res = await _request_get(session, track, artist, attempt_album, attempt_dur, timeout)
            if res == "not_found":
                break  # 404 — на этот запрос не нашлось, fallback к (b)
            if res is None:
                network_err = True
                if retry < retries:
                    await asyncio.sleep(0.8 * (retry + 1))
                    continue
                break
            # success
            data = res
            plain = data.get("plainLyrics") or None
            synced = data.get("syncedLyrics") or None
            timeline = _parse_lrc(synced) if synced else None
            return (
                Lyrics(
                    plain=plain,
                    synced=synced,
                    instrumental=bool(data.get("instrumental")),
                    source="lrclib",
                    timeline=timeline,
                ),
                network_err,
            )
    if network_err:
        _LOGGER.warning("lrclib all retries failed for %r — %r", track, artist)
    else:
        _LOGGER.debug("lrclib not_found %r — %r", track, artist)
    return None, network_err


def _norm(s: str) -> str:
    """Нормализация для нечёткого сравнения названий: только буквы/цифры, lower."""
    return re.sub(r"[^\w]+", "", s.lower(), flags=re.UNICODE)


def _pick_netease_song(
    songs: list[dict], track: str, artist: str, duration_sec: int | None
) -> int | None:
    """Выбор трека из выдачи поиска NetEase: совпадение названия и артиста,
    длительность в пределах допуска (если известна)."""
    want_track = _norm(track)
    want_artist = _norm(artist)
    for song in songs:
        if not isinstance(song, dict) or "id" not in song:
            continue
        name = _norm(str(song.get("name") or ""))
        if not name or (want_track not in name and name not in want_track):
            continue
        artists = [
            _norm(str(a.get("name") or ""))
            for a in (song.get("artists") or [])
            if isinstance(a, dict)
        ]
        if want_artist and artists and not any(
            a and (a in want_artist or want_artist in a) for a in artists
        ):
            continue
        dur_ms = song.get("duration")
        if duration_sec and isinstance(dur_ms, (int, float)) and dur_ms > 0:
            if abs(dur_ms / 1000 - duration_sec) > _NETEASE_DURATION_TOLERANCE_SEC:
                continue
        return int(song["id"])
    return None


async def _fetch_netease(
    session: aiohttp.ClientSession,
    track: str,
    artist: str,
    duration_sec: int | None,
    timeout: float,
) -> tuple[Lyrics | None, bool]:
    """NetEase: поиск трека → lyric-endpoint. (lyrics | None, network_err)."""
    try:
        async with session.get(
            NETEASE_SEARCH_URL,
            params={"s": f"{track} {artist}", "type": "1", "limit": "10", "offset": "0"},
            headers=_NETEASE_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            if r.status == 404:
                return None, False  # «не найдено», а не сбой — кэшируем маркер
            if r.status != 200:
                _LOGGER.debug("netease search HTTP %s for %r — %r", r.status, track, artist)
                return None, True
            data = await r.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
        _LOGGER.debug("netease search err: %s", exc.__class__.__name__)
        return None, True

    songs = ((data or {}).get("result") or {}).get("songs") or []
    song_id = _pick_netease_song(songs, track, artist, duration_sec)
    if song_id is None:
        _LOGGER.debug("netease not_found %r — %r", track, artist)
        return None, False

    try:
        async with session.get(
            NETEASE_LYRIC_URL,
            params={"id": str(song_id), "lv": "1", "kv": "-1", "tv": "-1"},
            headers=_NETEASE_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            if r.status != 200:
                return None, True
            data = await r.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
        _LOGGER.debug("netease lyric err: %s", exc.__class__.__name__)
        return None, True

    lrc = ((data or {}).get("lrc") or {}).get("lyric") or None
    if not lrc:
        return None, False
    # Вычищаем строки-кредиты ИЗ САМОГО LRC (а не только из timeline):
    # synced персистится в Store, и timeline пересобирается из него после
    # рестарта — фильтр только по timeline вернул бы кредиты из кэша.
    lrc = "\n".join(
        line for line in lrc.splitlines()
        if not _NETEASE_CREDIT_RE.match(_LRC_TS.sub("", line).strip())
    )
    timeline = _parse_lrc(lrc)
    if not timeline:
        # LRC без таймстампов — годится только как plain-текст.
        return Lyrics(plain=lrc, synced=None, instrumental=False,
                      source="netease", timeline=None), False
    plain = "\n".join(text for _ts, text in timeline if text) or None
    return Lyrics(plain=plain, synced=lrc, instrumental=False,
                  source="netease", timeline=timeline), False


async def fetch_lyrics(
    session: aiohttp.ClientSession,
    track: str,
    artist: str,
    album: str | None = None,
    duration_sec: int | None = None,
    timeout: float = 6.0,
    retries: int = 2,
    use_netease: bool = True,
) -> Lyrics | None:
    """Цепочка провайдеров: LRCLIB → NetEase (резерв).

    Приоритет — synced-текст: instrumental или synced от LRCLIB возвращаются
    сразу; plain-only результат придерживается как «лучший найденный», пока
    NetEase не даст synced. None — только при сетевых ошибках без результата
    (caller ретраит на следующем track-update); «нигде не нашли» — пустой
    Lyrics-маркер (кэшируется, чтобы не долбить API на каждый poll).
    """
    if not track or not artist:
        return None

    network_err = False
    best: Lyrics | None = None

    lrclib, err = await _fetch_lrclib(
        session, track, artist, album, duration_sec, timeout, retries
    )
    network_err |= err
    if lrclib is not None:
        if lrclib.synced or lrclib.instrumental:
            return lrclib
        best = lrclib  # plain-only — попробуем добыть synced у резерва

    if use_netease:
        netease, err = await _fetch_netease(session, track, artist, duration_sec, timeout)
        network_err |= err
        if netease is not None:
            if netease.synced:
                return netease
            best = best or netease

    if best is not None:
        return best
    if network_err:
        return None  # caller сможет ретраить позже
    return Lyrics(None, None, False, "lrclib", None)


def current_line(timeline: list[tuple[float, str]] | None, position_sec: float) -> str | None:
    """Возвращает строку, активную в данной позиции трека."""
    if not timeline:
        return None
    last: str | None = None
    for ts, text in timeline:
        if ts > position_sec:
            break
        if text:
            last = text
    return last
