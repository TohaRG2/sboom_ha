"""Тесты _timeline_at (camera.py) — позиционирование в LRC-таймлайне.

Регрессия из код-ревью: стрим сравнивал кадры по ТЕКСТУ строки — повторяющиеся
строки припева не обновляли кадр (next-line зависала). _timeline_at обязана
отдавать РАЗНЫЕ индексы для одинакового текста в разных точках таймлайна.
"""
from __future__ import annotations

from sboom_ha.camera import _timeline_at

# _fakes ставит HA-stubs до импорта sboom_ha.camera (там homeassistant.* imports).
import tests._fakes  # noqa: F401

# Припев повторяется: одинаковый текст в позициях 1 и 2.
TIMELINE = [
    (5.0, "Verse line"),
    (10.0, "Chorus"),
    (15.0, "Chorus"),
    (20.0, "Bridge"),
]


def test_repeated_chorus_lines_have_distinct_indices():
    """Одинаковый текст в соседних строках → разные idx (ключ обновления кадра)."""
    idx1, cur1, nxt1, _ = _timeline_at(TIMELINE, 12.0)
    idx2, cur2, nxt2, _ = _timeline_at(TIMELINE, 17.0)
    assert cur1 == cur2 == "Chorus"
    assert idx1 == 1 and idx2 == 2
    assert idx1 != idx2, "повторяющийся припев должен менять индекс, иначе кадр не обновится"
    # и next-line при этом реально отличается
    assert nxt1 == "Chorus"
    assert nxt2 == "Bridge"


def test_frac_is_linear_between_timestamps():
    """frac — линейная доля между таймстампом текущей и следующей строки."""
    _, _, _, frac = _timeline_at(TIMELINE, 12.5)  # между 10 и 15
    assert frac == (12.5 - 10.0) / (15.0 - 10.0) == 0.5


def test_frac_zero_at_exact_line_start():
    idx, cur, _, frac = _timeline_at(TIMELINE, 10.0)
    assert idx == 1 and cur == "Chorus"
    assert frac == 0.0


def test_last_line_has_no_frac():
    """Для последней строки нет следующего таймстампа — frac None, заливки нет."""
    idx, cur, nxt, frac = _timeline_at(TIMELINE, 25.0)
    assert idx == 3
    assert cur == "Bridge"
    assert nxt is None
    assert frac is None


def test_before_first_line_idx_minus_one():
    """До первой строки: idx=-1, current нет, next — первая строка, frac None."""
    idx, cur, nxt, frac = _timeline_at(TIMELINE, 2.0)
    assert idx == -1
    assert cur is None
    assert nxt == "Verse line"
    assert frac is None


def test_empty_timeline():
    idx, cur, nxt, frac = _timeline_at([], 10.0)
    assert (idx, cur, nxt, frac) == (-1, None, None, None)


# ────────────────── _stream_idle: первый кадр без позиции (аудит #9) ──────


import asyncio  # noqa: E402
import contextlib  # noqa: E402

import pytest  # noqa: E402

from tests._fakes import build_coordinator, make_track  # noqa: E402


class _RecordingResponse:
    """Фейковый StreamResponse: записывает первый кадр и обрывает стрим."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.writes.append(data)
        raise ConnectionResetError  # завершить стрим после первого кадра


@pytest.mark.asyncio
async def test_stream_idle_writes_first_frame_without_position():
    """BT-трек без позиции: раньше cur_sec==last_sec==None навсегда — 0 кадров."""
    from sboom_ha.camera import SboomLyricsCamera

    coord = build_coordinator(
        track=make_track(
            title="BT Song", position_sec=None, duration_sec=None,
            track_id=None, provider=None, release_id=None, artist_ids=[],
        )
    )
    cam = SboomLyricsCamera(coord, coord.entry)
    resp = _RecordingResponse()
    with contextlib.suppress(ConnectionResetError, asyncio.TimeoutError):
        await asyncio.wait_for(cam._stream_idle(resp), timeout=2.5)
    assert resp.writes, "первый кадр обязан отрисоваться даже без позиции (BT)"


# ────────── негативный кэш обложки при лежащем CDN (аудит #39) ──────────


@pytest.mark.asyncio
async def test_failed_cover_download_not_retried_every_frame():
    """Idle-стрим зовёт _fetch_cover_raw каждый кадр: при лежащем CDN
    неудачный URL кэшируется на время, а не качается заново раз в секунду."""
    from sboom_ha.camera import SboomLyricsCamera

    coord = build_coordinator(track=make_track())  # zvuk-трек: cover_url есть
    cam = SboomLyricsCamera(coord, coord.entry)
    calls: list[str] = []

    async def failing_download(url: str):
        calls.append(url)
        return None

    cam._download_cover = failing_download
    await cam._fetch_cover_raw(coord.track)
    await cam._fetch_cover_raw(coord.track)
    assert len(calls) == 1, "повторный кадр не должен качать лежащий CDN заново"
