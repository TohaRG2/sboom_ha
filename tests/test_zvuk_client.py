"""Тесты standalone-клиента Sber Звук (`zvuk_client`).

Клиент не импортирует Home Assistant — тестируем напрямую с `respx`
(мок httpx). Контракты, которые ловят реальные регрессии:
- разбор zvuk-URL / сборка staros-deeplink (tid vs pid),
- нормализация элементов поиска (подпись/тип/pt),
- категоризированный `search()` + обогащение обложками,
- извлечение доминирующего цвета обложки (ambient-glow панели).
"""
from __future__ import annotations

import io

import httpx
import pytest
import respx
from PIL import Image
from sboom_ha.zvuk_client import ZvukClient, _dominant_color_from_bytes

PROFILE = "https://zvuk.com/api/tiny/profile"
SEARCH = "https://zvuk.com/api/tiny/search"
GRAPHQL = "https://zvuk.com/api/v1/graphql"


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    """Сплошной PNG заданного цвета."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


# ── чистые статические методы (без сети) ────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://zvuk.com/track/84279897", ("track", "tid", "84279897")),
        ("https://zvuk.com/artist/126769660", ("artist", "pid", "126769660")),
        ("https://zvuk.com/release/14359015", ("release", "pid", "14359015")),
        ("https://zvuk.com/playlist/10968273", ("playlist", "pid", "10968273")),
        ("нет ссылки", None),
    ],
)
def test_parse_zvuk_url(url, expected):
    assert ZvukClient.parse_zvuk_url(url) == expected


def test_parse_zvuk_url_bare_id_with_kind():
    assert ZvukClient.parse_zvuk_url("84279897", kind="track") == (
        "track",
        "tid",
        "84279897",
    )
    # голый id без kind распознать нельзя
    assert ZvukClient.parse_zvuk_url("84279897") is None


def test_build_deeplink_tid_vs_pid():
    assert (
        ZvukClient.build_deeplink("track", "tid", "1")
        == "staros://music?tid=1&pt=track"
    )
    assert (
        ZvukClient.build_deeplink("artist", "pid", "2")
        == "staros://music?pid=2&pt=artist"
    )


def test_search_item_subtitle_by_type():
    from sboom_ha.zvuk_client import ZvukClient as Z

    track = Z._search_item({"id": 1, "title": "T", "aname": "A"}, "track", "track")
    assert track["subtitle"] == "A"
    assert track["type"] == "track" and track["pt"] == "track"

    artist = Z._search_item({"id": 2, "title": "A"}, "artist", "artist")
    assert artist["subtitle"] == "Исполнитель"

    rel = Z._search_item({"id": 3, "title": "R", "aname": "A"}, "release", "release")
    assert rel["subtitle"] == "A · Альбом"

    pl = Z._search_item(
        {"id": 4, "title": "P", "tracks_number": 38}, "playlist", "playlist"
    )
    assert "38" in pl["subtitle"]


# ── извлечение цвета ────────────────────────────────────────────────────────


def test_dominant_color_solid_red():
    hexcolor = _dominant_color_from_bytes(_png_bytes((220, 20, 20)))
    assert hexcolor is not None and hexcolor.startswith("#") and len(hexcolor) == 7
    r = int(hexcolor[1:3], 16)
    g = int(hexcolor[3:5], 16)
    # красный доминирует
    assert r > g


# ── сетевые сценарии (respx) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_token_cached():
    async with respx.mock:
        route = respx.get(PROFILE).mock(
            return_value=httpx.Response(200, json={"result": {"token": "TOK"}})
        )
        client = ZvukClient()
        assert await client.get_token() == "TOK"
        assert await client.get_token() == "TOK"  # кеш
        await client.aclose()
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_search_categorized_with_covers():
    search_json = {
        "result": {
            "search": {
                "best_item": {"doc_type": "track", "id": 84279911},
                "artists": {"items": [{"id": 126769660, "title": "Егор Летов"}]},
                "releases": {
                    "items": [{"id": 14359015, "title": "Альбом", "aname": "Летов"}]
                },
                "tracks": {
                    "items": [
                        {"id": 84279911, "title": "Трек", "aname": "Летов"}
                    ]
                },
                "playlists": {"items": []},
            }
        }
    }
    graphql_data = {
        "data": {
            "getArtists": [
                {
                    "id": "126769660",
                    "image": {"src": "https://cdn/pic?id=1&size={size}"},
                }
            ],
            "getReleases": [
                {
                    "id": "14359015",
                    "image": {"src": "https://cdn/pic?id=2&size={size}"},
                }
            ],
            "getTracks": [
                {
                    "id": "84279911",
                    "title": "Трек",
                    "artists": [{"id": "1", "title": "Летов"}],
                    "release": {
                        "id": "14359015",
                        "title": "Альбом",
                        "image": {"src": "https://cdn/pic?id=3&size={size}"},
                    },
                }
            ],
        }
    }
    async with respx.mock:
        respx.get(PROFILE).mock(
            return_value=httpx.Response(200, json={"result": {"token": "T"}})
        )
        respx.get(SEARCH).mock(
            return_value=httpx.Response(200, json=search_json)
        )
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json=graphql_data)
        )

        client = ZvukClient()
        res = await client.search("Летов", limit=8)
        await client.aclose()

    assert res["best"] == {"type": "track", "id": "84279911"}
    assert len(res["artists"]) == 1 and len(res["tracks"]) == 1
    art = res["artists"][0]
    assert art["pt"] == "artist" and art["cover_url"] == "https://cdn/pic?id=1&size=400x400"
    assert res["tracks"][0]["cover_url"].endswith("size=400x400")


@pytest.mark.asyncio
async def test_search_first_deeplink_prefers_best():
    search_json = {
        "result": {
            "search": {
                "best_item": {"doc_type": "artist", "id": 126769660},
                "artists": {"items": [{"id": 126769660, "title": "Летов"}]},
                "releases": {"items": []},
                "tracks": {"items": []},
                "playlists": {"items": []},
            }
        }
    }
    async with respx.mock:
        respx.get(PROFILE).mock(
            return_value=httpx.Response(200, json={"result": {"token": "T"}})
        )
        respx.get(SEARCH).mock(
            return_value=httpx.Response(200, json=search_json)
        )
        respx.post(GRAPHQL).mock(
            return_value=httpx.Response(200, json={"data": {"getArtists": []}})
        )
        client = ZvukClient()
        dl = await client.search_first_deeplink("Летов")
        await client.aclose()
    assert dl == "staros://music?pid=126769660&pt=artist"


@pytest.mark.asyncio
async def test_dominant_cover_color_cached():
    async with respx.mock:
        respx.get(PROFILE).mock(
            return_value=httpx.Response(200, json={"result": {"token": "T"}})
        )
        img = respx.get("https://cdn/cover.png").mock(
            return_value=httpx.Response(200, content=_png_bytes((30, 180, 90)))
        )
        client = ZvukClient()
        c1 = await client.dominant_cover_color("https://cdn/cover.png")
        c2 = await client.dominant_cover_color("https://cdn/cover.png")
        await client.aclose()
    assert c1 == c2 and c1.startswith("#")
    assert img.call_count == 1  # кеш по URL
