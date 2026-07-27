"""Тесты для helpers.cover_url и track_position.

Фокус: edge-cases которые реально приходят с колонки —
- провайдер не zvuk
- release_id отсутствует, есть только artist_ids
- timestamp в будущем (clock-skew)
- огромная дельта (после reboot колонки)
- позиция уже за пределом длительности (closed track)
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from sboom_ha.api import TrackInfo
from sboom_ha.helpers import _MAX_EXTRAPOLATION_SEC, cover_url, track_position

# ─────────────────────────── cover_url ───────────────────────────

def _make_track(**kwargs):
    defaults = {
        "track_id": "1",
        "title": "T",
        "artists": ["A"],
        "album": "Alb",
        "release_id": None,
        "artist_ids": [],
        "provider": "zvuk",
        "playing": True,
        "position_sec": 0,
        "position_ts_ms": None,
        "duration_sec": 100,
        "shuffle": False,
        "repeat": "none",
    }
    defaults.update(kwargs)
    # Реальный TrackInfo, а не SimpleNamespace: тест ловит переименования
    # и добавления полей модели (received_monotonic и т.п.).
    return TrackInfo(**defaults)


def test_cover_url_returns_release_url_when_release_id_present():
    track = _make_track(release_id="999", artist_ids=["7"])
    url = cover_url(track)
    assert "type=release" in url
    assert "id=999" in url
    assert "size=600x600" in url


def test_cover_url_falls_back_to_artist_when_no_release_id():
    """Это РЕАЛЬНЫЙ кейс — например при VOD-стриме release_id может не приходить."""
    track = _make_track(release_id=None, artist_ids=["42", "43"])
    url = cover_url(track)
    assert "type=artist" in url
    assert "id=42" in url, "Берём первого артиста"


def test_cover_url_returns_none_for_non_zvuk_provider():
    """salute / spotify / любой другой провайдер не имеет нашего CDN."""
    track = _make_track(provider="salute", release_id="999")
    assert cover_url(track) is None


def test_cover_url_returns_none_when_neither_release_nor_artist():
    """Без идентификаторов URL построить нельзя."""
    track = _make_track(release_id=None, artist_ids=[])
    assert cover_url(track) is None


def test_cover_url_handles_none_track():
    assert cover_url(None) is None


# ─────────────────────────── track_position ───────────────────────────

class _Coord:
    def __init__(self, track):
        self.track = track


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def test_track_position_none_when_no_track():
    assert track_position(_Coord(None)) is None


def test_track_position_returns_position_when_paused_no_extrapolation():
    """На паузе — позиция не должна "уезжать", даже с position_ts_ms."""
    track = _make_track(playing=False, position_sec=42, position_ts_ms=_now_ms() - 60_000)
    assert track_position(_Coord(track)) == 42


def test_track_position_extrapolates_when_playing():
    """Между push'ами от колонки позиция должна расти по wall-clock."""
    track = _make_track(
        playing=True,
        position_sec=100,
        duration_sec=300,  # длинный трек чтобы не сработал clamp
        position_ts_ms=_now_ms() - 5_000,  # 5 секунд назад был push
    )
    pos = track_position(_Coord(track))
    assert 104.5 <= pos <= 106.5, f"Должно быть ~105 (100 + 5 сек), получено {pos}"


def test_track_position_clamps_to_duration():
    """Если экстраполяция уехала за конец трека — clamp."""
    track = _make_track(
        playing=True,
        position_sec=95,
        duration_sec=100,
        position_ts_ms=_now_ms() - 30_000,  # 30 сек назад — экстраполяция = 125
    )
    pos = track_position(_Coord(track))
    assert pos == 100, "Не должна превышать длительность"


def test_track_position_ignores_huge_delta_after_reboot():
    """После reboot колонки position_ts_ms может быть мусорный (или из старой эпохи)."""
    track = _make_track(
        playing=True,
        position_sec=42,
        position_ts_ms=_now_ms() - (_MAX_EXTRAPOLATION_SEC + 100) * 1000,
    )
    pos = track_position(_Coord(track))
    assert pos == 42, "При большой дельте не экстраполируем — иначе позиция уйдёт в космос"


def test_track_position_ignores_negative_delta_clock_skew():
    """Если position_ts_ms в будущем (skew между HA и колонкой) — не вычитаем."""
    track = _make_track(
        playing=True,
        position_sec=10,
        position_ts_ms=_now_ms() + 30_000,  # на 30 сек в будущем
    )
    pos = track_position(_Coord(track))
    assert pos == 10, "Negative delta не должна давать отрицательную экстраполяцию"


def test_track_position_handles_no_duration():
    """Когда duration_sec=None (live-stream) — clamp пропускается, но возвращаем позицию."""
    track = _make_track(
        playing=True, position_sec=50, duration_sec=None,
        position_ts_ms=_now_ms() - 2_000,
    )
    pos = track_position(_Coord(track))
    assert 51.5 <= pos <= 52.5


# ────────────────── экстраполяция от received_monotonic ──────────────────
# Регрессия из ревью: завязка на часы колонки (position_ts_ms) сдвигала
# позицию на величину clock skew, а при часах колонки «в будущем» вовсе
# отключала экстраполяцию. База — момент получения данных на стороне HA.

def test_track_position_uses_received_monotonic_over_device_clock():
    """Часы колонки на 30 сек в будущем — раньше это замораживало позицию.
    С received_monotonic позиция растёт от момента получения push'а."""
    track = _make_track(
        playing=True,
        position_sec=100,
        duration_sec=300,
        position_ts_ms=_now_ms() + 30_000,       # skew: колонка «в будущем»
        received_monotonic=time.monotonic() - 5,  # push пришёл 5 сек назад
    )
    pos = track_position(_Coord(track))
    assert 104.5 <= pos <= 106.5, f"Ожидалось ~105 (100 + 5 сек по HA-часам), получено {pos}"


def test_track_position_scales_with_playback_speed():
    """При скорости 2.0 позиция растёт вдвое быстрее wall-clock."""
    track = _make_track(
        playing=True,
        position_sec=100,
        duration_sec=300,
        playback_speed=2.0,
        received_monotonic=time.monotonic() - 5,
    )
    pos = track_position(_Coord(track))
    assert 109 <= pos <= 112, f"Ожидалось ~110 (100 + 5 сек × 2.0), получено {pos}"


def test_track_position_paused_ignores_received_monotonic():
    """На паузе позиция не растёт и от HA-часов."""
    track = _make_track(
        playing=False,
        position_sec=42,
        received_monotonic=time.monotonic() - 60,
    )
    assert track_position(_Coord(track)) == 42
