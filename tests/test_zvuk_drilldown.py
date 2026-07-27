"""Тесты drill-down методов клиента Sber Звук (`get_artist` / `get_release`).

Drill-down браузер панели ходит в GraphQL `getArtists`/`getReleases` с
расширенными выборками (releases + popularTracks у артиста, полный треклист у
релиза). Клиент standalone — мокаем сеть через `respx` (httpx) и проверяем
контракты нормализации, которые ловят реальные регрессии:
- releases артиста: год из ISO-даты, подпись «Альбом/Сингл · YYYY», pt/cover,
  сохранение порядка (новые сверху задаёт сервер);
- popularTracks / release.tracks: плоский трек (`_flat_track`) с artists как
  список строк, duration/explicit/has_flac, обложка = обложка релиза (400x400);
- шапка релиза: year, artist (join имён), cover;
- статический `_year` и деградация на пустой/битый ответ → None.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from sboom_ha.zvuk_client import ZvukClient

PROFILE = "https://zvuk.com/api/tiny/profile"
GRAPHQL = "https://zvuk.com/api/v1/graphql"


def _token_route() -> None:
    """Замокать выдачу анонимного токена /api/tiny/profile."""
    respx.get(PROFILE).mock(
        return_value=httpx.Response(200, json={"result": {"token": "T"}})
    )


# ── статический _year (без сети) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1998-05-15T00:00:00", 1998),
        ("2021", 2021),
        ("2021-01-01", 2021),
        ("не дата", None),
        ("", None),
        (None, None),
        (2021, None),  # не str
    ],
)
def test_year_parsing(value, expected):
    assert ZvukClient._year(value) == expected


# ── get_artist ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_artist_normalizes_releases_and_tracks():
    graphql_data = {
        "data": {
            "getArtists": [
                {
                    "id": "126769660",
                    "title": "Егор Летов",
                    "image": {"src": "https://cdn/pic?id=art&size={size}"},
                    "releases": [
                        {
                            "id": "111",
                            "title": "Сто лет одиночества",
                            "date": "1993-04-01T00:00:00",
                            "type": "album",
                            "image": {"src": "https://cdn/pic?id=r1&size={size}"},
                        },
                        {
                            "id": "222",
                            "title": "Прыг-скок",
                            "date": "1990-06-15T00:00:00",
                            "type": "single",
                            "image": {"src": "https://cdn/pic?id=r2&size={size}"},
                        },
                    ],
                    "popularTracks": [
                        {
                            "id": "84279911",
                            "title": "Всё идёт по плану",
                            "duration": 210,
                            "explicit": True,
                            "artists": [{"title": "Гражданская оборона"}],
                            "release": {
                                "image": {"src": "https://cdn/pic?id=t1&size={size}"}
                            },
                        }
                    ],
                }
            ]
        }
    }
    async with respx.mock:
        _token_route()
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json=graphql_data)
        )
        client = ZvukClient()
        res = await client.get_artist("126769660")
        await client.aclose()

    assert res is not None
    assert res["type"] == "artist"
    assert res["id"] == "126769660"
    assert res["title"] == "Егор Летов"
    assert res["cover_url"] == "https://cdn/pic?id=art&size=400x400"

    # порядок релизов сохранён (как отдал сервер)
    releases = res["releases"]
    assert [r["id"] for r in releases] == ["111", "222"]

    alb = releases[0]
    assert alb["type"] == "release" and alb["pt"] == "release"
    assert alb["year"] == 1993
    assert alb["subtitle"] == "Альбом · 1993"
    assert alb["release_type"] == "album"
    assert alb["cover_url"] == "https://cdn/pic?id=r1&size=400x400"

    sng = releases[1]
    assert sng["year"] == 1990
    assert sng["subtitle"] == "Сингл · 1990"
    assert sng["release_type"] == "single"

    # треки нормализованы (плоские, artists как список строк, обложка релиза)
    tracks = res["tracks"]
    assert len(tracks) == 1
    tr = tracks[0]
    assert tr["type"] == "track" and tr["pt"] == "track"
    assert tr["id"] == "84279911"
    assert tr["artists"] == ["Гражданская оборона"]
    assert tr["duration"] == 210
    assert tr["explicit"] is True
    assert tr["cover_url"] == "https://cdn/pic?id=t1&size=400x400"


@pytest.mark.asyncio
async def test_get_artist_release_without_date_has_bare_label():
    graphql_data = {
        "data": {
            "getArtists": [
                {
                    "id": "1",
                    "title": "A",
                    "image": {"src": "https://cdn/pic?id=a&size={size}"},
                    "releases": [
                        {"id": "9", "title": "R", "type": "album", "image": {}}
                    ],
                    "popularTracks": [],
                }
            ]
        }
    }
    async with respx.mock:
        _token_route()
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json=graphql_data)
        )
        client = ZvukClient()
        res = await client.get_artist("1")
        await client.aclose()

    rel = res["releases"][0]
    assert rel["year"] is None
    assert rel["subtitle"] == "Альбом"  # без года — только метка
    assert rel["cover_url"] is None
    assert res["tracks"] == []


@pytest.mark.asyncio
async def test_get_artist_empty_result_returns_none():
    async with respx.mock:
        _token_route()
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json={"data": {"getArtists": []}})
        )
        client = ZvukClient()
        res = await client.get_artist("126769660")
        await client.aclose()
    assert res is None


@pytest.mark.asyncio
async def test_get_artist_blank_id_returns_none():
    client = ZvukClient()
    assert await client.get_artist("") is None
    await client.aclose()


# ── get_release ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_release_header_and_tracklist():
    graphql_data = {
        "data": {
            "getReleases": [
                {
                    "id": "14359015",
                    "title": "Прыг-скок",
                    "date": "1990-01-01T00:00:00",
                    "type": "album",
                    "image": {"src": "https://cdn/pic?id=rel&size={size}"},
                    "artists": [
                        {"id": "1", "title": "Егор Летов"},
                        {"id": "2", "title": "Кузя УО"},
                    ],
                    "tracks": [
                        {
                            "id": "501",
                            "title": "Прыг-скок",
                            "duration": 180,
                            "explicit": False,
                            "hasFlac": True,
                            "artists": [{"title": "Егор Летов"}],
                        },
                        {
                            "id": "502",
                            "title": "Про дурачка",
                            "duration": 240,
                            "explicit": True,
                            "hasFlac": False,
                            "artists": [{"title": "Егор Летов"}],
                        },
                    ],
                }
            ]
        }
    }
    async with respx.mock:
        _token_route()
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json=graphql_data)
        )
        client = ZvukClient()
        res = await client.get_release("14359015")
        await client.aclose()

    assert res is not None
    assert res["type"] == "release"
    assert res["id"] == "14359015"
    assert res["year"] == 1990
    assert res["release_type"] == "album"
    assert res["cover_url"] == "https://cdn/pic?id=rel&size=400x400"
    # artist — join имён исполнителей
    assert res["artist"] == "Егор Летов, Кузя УО"

    tracks = res["tracks"]
    assert [t["id"] for t in tracks] == ["501", "502"]

    t0 = tracks[0]
    assert t0["duration"] == 180
    assert t0["explicit"] is False
    assert t0["has_flac"] is True
    # обложка трека = обложка релиза (передана явно в _flat_track)
    assert t0["cover_url"] == "https://cdn/pic?id=rel&size=400x400"

    t1 = tracks[1]
    assert t1["explicit"] is True
    assert t1["has_flac"] is False
    assert t1["cover_url"] == "https://cdn/pic?id=rel&size=400x400"


@pytest.mark.asyncio
async def test_get_release_empty_result_returns_none():
    async with respx.mock:
        _token_route()
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json={"data": {"getReleases": []}})
        )
        client = ZvukClient()
        res = await client.get_release("14359015")
        await client.aclose()
    assert res is None


@pytest.mark.asyncio
async def test_get_release_blank_id_returns_none():
    client = ZvukClient()
    assert await client.get_release("") is None
    await client.aclose()
