"""Тесты поиска обложки по title+artist (iTunes → Deezer) + CoverManager.

Реальные кейсы:
- iTunes: артворк апскейлится 100→600
- iTunes пусто/не-200 → fallback на Deezer
- оба пусто → None; пустой title/artist → без запроса
- CoverManager: волатильный слот; каталог (track_id) НЕ трогается; радио —
  трогается (обложке синк не нужен, в отличие от лирики)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from sboom_ha.cover_client import (
    DEEZER_SEARCH_URL,
    ITUNES_SEARCH_URL,
    fetch_cover,
)
from sboom_ha.cover_manager import _cover_key

from tests._fakes import build_coordinator, make_track


def _resp(status: int, json_data):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _session_by_url(mapping: dict):
    """Сессия, отдающая разный ответ по URL (для проверки fallback iTunes→Deezer)."""
    session = MagicMock(spec=aiohttp.ClientSession)

    def dispatch(url, **kwargs):
        for base, ctx in mapping.items():
            if url == base:
                return ctx
        return _resp(404, None)

    session.get = MagicMock(side_effect=dispatch)
    return session


# ─────────────────────── fetch_cover ───────────────────────

@pytest.mark.asyncio
async def test_empty_title_or_artist_no_request():
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock()
    assert await fetch_cover(session, "", "Artist") is None
    assert await fetch_cover(session, "Song", "") is None
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_itunes_hit_upscales_artwork_to_600():
    itunes = _resp(200, {"resultCount": 1, "results": [
        {"artworkUrl100": "https://is1.mzstatic.com/a/b/100x100bb.jpg"},
    ]})
    session = _session_by_url({ITUNES_SEARCH_URL: itunes})
    url = await fetch_cover(session, "Song", "Artist")
    assert url == "https://is1.mzstatic.com/a/b/600x600bb.jpg"  # 100→600


@pytest.mark.asyncio
async def test_falls_back_to_deezer_when_itunes_empty():
    itunes = _resp(200, {"resultCount": 0, "results": []})
    deezer = _resp(200, {"data": [
        {"album": {"cover_xl": "https://e-cdn/cover/xl.jpg",
                   "cover_big": "https://e-cdn/cover/big.jpg"}},
    ]})
    session = _session_by_url({ITUNES_SEARCH_URL: itunes, DEEZER_SEARCH_URL: deezer})
    url = await fetch_cover(session, "Song", "Artist")
    assert url == "https://e-cdn/cover/xl.jpg"  # cover_xl приоритетнее big


@pytest.mark.asyncio
async def test_itunes_non_200_falls_back_to_deezer():
    itunes = _resp(503, None)
    deezer = _resp(200, {"data": [{"album": {"cover_big": "https://e-cdn/big.jpg"}}]})
    session = _session_by_url({ITUNES_SEARCH_URL: itunes, DEEZER_SEARCH_URL: deezer})
    assert await fetch_cover(session, "Song", "Artist") == "https://e-cdn/big.jpg"


@pytest.mark.asyncio
async def test_both_providers_empty_returns_none():
    itunes = _resp(200, {"results": []})
    deezer = _resp(200, {"data": []})
    session = _session_by_url({ITUNES_SEARCH_URL: itunes, DEEZER_SEARCH_URL: deezer})
    assert await fetch_cover(session, "Song", "Artist") is None


@pytest.mark.asyncio
async def test_itunes_error_does_not_abort_deezer():
    """Исключение в iTunes не роняет весь поиск — Deezer всё равно пробуется."""
    deezer = _resp(200, {"data": [{"album": {"cover_xl": "https://e-cdn/xl.jpg"}}]})
    session = MagicMock(spec=aiohttp.ClientSession)

    def dispatch(url, **kwargs):
        if url == ITUNES_SEARCH_URL:
            raise aiohttp.ClientError("boom")
        return deezer

    session.get = MagicMock(side_effect=dispatch)
    assert await fetch_cover(session, "Song", "Artist") == "https://e-cdn/xl.jpg"


# ─────────────────────── CoverManager._cover_key ───────────────────────

def test_cover_key_skips_catalog_includes_bt_and_radio():
    """Каталог (track_id) → None (обложка через CDN). BT и радио → ключ есть."""
    assert _cover_key(make_track(track_id="1001")) is None  # каталог
    assert _cover_key(make_track(title="S", artists=["A"], track_id=None)) == "s|a"
    radio = make_track(title="Song", artists=["Band"], track_id=None)
    radio.media_source = "RADIO"
    assert _cover_key(radio) == "song|band"  # радио НЕ исключается (нужна лишь картинка)
    assert _cover_key(make_track(title="S", artists=[], track_id=None)) is None


def test_cover_manager_current_for_uses_volatile_slot():
    cover = build_coordinator().cover
    bt = make_track(title="I Put a Spell on You", artists=["Bonnie Tyler"], track_id=None)
    assert cover.current_for(bt) is None
    cover._key = _cover_key(bt)
    cover._url = "https://cdn/art.jpg"
    assert cover.current_for(bt) == "https://cdn/art.jpg"
    assert cover.current_for(make_track(title="Other", artists=["X"], track_id=None)) is None


@pytest.mark.asyncio
async def test_cover_manager_fetch_sets_slot(monkeypatch):
    async def fake(*a, **k):
        return "https://cdn/found.jpg"
    monkeypatch.setattr("sboom_ha.cover_manager.fetch_cover", fake)
    cover = build_coordinator().cover
    await cover._fetch("s|a", "S", "A")
    assert cover._url == "https://cdn/found.jpg"
    assert cover._key == "s|a"
