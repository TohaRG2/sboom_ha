"""Тесты персиста lyrics-кеша через HA Store."""
from __future__ import annotations

import pytest
from sboom_ha.lyrics_client import Lyrics

from tests._fakes import build_coordinator


def _lyrics(plain: str = "text") -> Lyrics:
    return Lyrics(plain=plain, synced=None, instrumental=False,
                  source="lrclib", timeline=None)


def test_lyrics_cache_data_serialises_and_skips_none():
    """Снимок кеша содержит только реальные Lyrics, None-записи отбрасываются."""
    coord = build_coordinator()
    coord.lyrics.by_track = {"1": _lyrics("a"), "2": None, "3": _lyrics("b")}
    data = coord.lyrics.cache_snapshot()
    assert set(data.keys()) == {"1", "3"}
    assert data["1"]["plain"] == "a"


@pytest.mark.asyncio
async def test_load_lyrics_cache_populates_from_store():
    coord = build_coordinator()
    coord.lyrics._store._data = {
        "42": {"plain": "hello", "synced": None,
               "instrumental": False, "source": "lrclib"},
    }
    await coord.lyrics.async_load()
    assert coord.lyrics.by_track["42"].plain == "hello"


@pytest.mark.asyncio
async def test_load_lyrics_cache_empty_store_starts_clean():
    """Store пуст (async_load → None) — стартуем с пустым кешем, без падения."""
    coord = build_coordinator()
    await coord.lyrics.async_load()
    assert coord.lyrics.by_track == {}


@pytest.mark.asyncio
async def test_load_lyrics_cache_skips_garbage_entries():
    coord = build_coordinator()
    coord.lyrics._store._data = {
        "ok": {"plain": "x", "instrumental": False, "source": "lrclib"},
        "bad": "not a dict",
    }
    await coord.lyrics.async_load()
    assert "ok" in coord.lyrics.by_track
    assert "bad" not in coord.lyrics.by_track


# ─────────────── волатильная лирика для BT/радио (без диск-кэша) ───────────────

from sboom_ha.lyrics_manager import _synthetic_key  # noqa: E402

from tests._fakes import make_track  # noqa: E402


def test_synthetic_key_from_title_and_artists():
    """Ключ некаталожного трека: title|artists (lower); без исполнителя — None."""
    assert _synthetic_key(make_track(title="Song", artists=["A", "B"], track_id=None)) == "song|a,b"
    assert _synthetic_key(make_track(title="Song", artists=[], track_id=None)) is None
    assert _synthetic_key(make_track(title=None, artists=["A"], track_id=None)) is None


def test_synthetic_key_excludes_radio():
    """Радио исключено из караоке: позиция эфирная, синк невозможен → ключ None."""
    radio = make_track(title="Mama, I'm Coming Home", artists=["Ozzy Osbourne"],
                       track_id=None)
    radio.media_source = "RADIO"
    assert _synthetic_key(radio) is None
    bt = make_track(title="Song", artists=["Artist"], track_id=None)
    bt.media_source = "BLUETOOTH"
    assert _synthetic_key(bt) == "song|artist"  # BT — не радио, ключ есть


def test_current_for_bt_track_uses_volatile_slot_not_catalog():
    """BT/радио (track_id=None) обслуживается волатильным слотом по synthetic-ключу."""
    lm = build_coordinator().lyrics
    bt = make_track(title="I Put a Spell on You", artists=["Bonnie Tyler"], track_id=None)
    assert lm.current_for(bt) is None                 # ещё не загружено
    lm._volatile_key = _synthetic_key(bt)
    lm._volatile = _lyrics("bt lyrics")
    assert lm.current_for(bt).plain == "bt lyrics"
    # другой некаталожный трек — ключ не совпал, слот не отдаётся
    assert lm.current_for(make_track(title="Other", artists=["X"], track_id=None)) is None


def test_volatile_lyrics_never_persisted_to_store():
    """Волатильная лирика (BT/радио) не попадает в снимок для Store."""
    lm = build_coordinator().lyrics
    lm._volatile_key = "song|artist"
    lm._volatile = _lyrics("ephemeral")
    lm.by_track = {"555": _lyrics("catalog")}
    assert set(lm.cache_snapshot()) == {"555"}         # только каталожное


@pytest.mark.asyncio
async def test_fetch_volatile_sets_slot_without_touching_catalog(monkeypatch):
    """_fetch_volatile кладёт результат в слот и НЕ трогает каталожный кэш/Store."""
    async def fake_fetch(*a, **k):
        return _lyrics("fetched")
    monkeypatch.setattr("sboom_ha.lyrics_manager.fetch_lyrics", fake_fetch)
    lm = build_coordinator().lyrics
    await lm._fetch_volatile("k|v", "T", "A", None, None)
    assert lm._volatile_key == "k|v"
    assert lm._volatile.plain == "fetched"
    assert lm.by_track == {}                            # каталог не тронут
    assert lm.cache_snapshot() == {}                    # в Store ничего не уйдёт


@pytest.mark.asyncio
async def test_fetch_volatile_network_error_leaves_slot_empty(monkeypatch):
    """Сетевая ошибка (fetch→None) не выставляет слот — будет retry."""
    async def fake_fetch(*a, **k):
        return None
    monkeypatch.setattr("sboom_ha.lyrics_manager.fetch_lyrics", fake_fetch)
    lm = build_coordinator().lyrics
    await lm._fetch_volatile("k|v", "T", "A", None, None)
    assert lm._volatile_key is None
    assert lm._volatile is None
