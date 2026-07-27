"""Тесты state-merge и страховки от half-open в SboomCoordinator.

Регрессии из код-ревью:
- битый/частичный push обнулял громкость (нет merge со старым state);
- half-open сокет: все запросы таймаутят, но соединение «живое» —
  после N подряд полностью неудачных poll-циклов нужен принудительный close().
"""
from __future__ import annotations

import pytest
from sboom_ha._models import DeviceState
from sboom_ha.api import SpeakerState
from sboom_ha.const import POLL_FAILURES_BEFORE_RECONNECT

from tests._fakes import build_coordinator, make_state, make_track

# ─────────────────────── _merge_state ───────────────────────

def test_merge_state_partial_push_keeps_old_volume():
    """Регрессия: push без volume-блока обнулял громкость/mute в UI."""
    coord = build_coordinator(track=make_track(), state=make_state(volume=42, muted=True))
    merged = coord._merge_state(SpeakerState(volume_percent=None, muted=None))
    assert merged is not None
    assert merged.volume_percent == 42
    assert merged.muted is True


def test_merge_state_none_returns_previous_state_unchanged():
    """parse_state → None (нераспознанный payload) — старый state целиком."""
    old = make_state(volume=17, muted=False)
    coord = build_coordinator(track=make_track(), state=old)
    assert coord._merge_state(None) is old


def test_merge_state_new_values_replace_old():
    coord = build_coordinator(track=make_track(), state=make_state(volume=42, muted=True))
    merged = coord._merge_state(SpeakerState(volume_percent=80, muted=False))
    assert merged.volume_percent == 80
    assert merged.muted is False


def test_merge_state_keeps_old_device_when_missing_in_new():
    """device-сенсоры не должны пропадать из-за push без device-блока."""
    device = DeviceState(led_brightness=90)
    old = SpeakerState(volume_percent=10, muted=False, device=device)
    coord = build_coordinator(track=make_track(), state=old)
    merged = coord._merge_state(SpeakerState(volume_percent=20))
    assert merged.device is device
    assert merged.volume_percent == 20


def test_merge_state_no_previous_state_passthrough():
    """Первый state (прежнего нет) — частичный payload принимается как есть."""
    coord = build_coordinator(track=make_track(), state=None)
    merged = coord._merge_state(SpeakerState(volume_percent=None, muted=None))
    assert merged is not None
    assert merged.volume_percent is None


# ─────────────────────── poll failures → форс-reconnect ───────────────────────

def _patch_client(coord, *, state_ok: bool = False):
    """Мокает методы клиента: всё падает (или get_state успешен), close пишется в список."""
    closed: list[bool] = []

    async def fail(*args, **kwargs):
        raise ConnectionError("half-open: request timed out")

    async def ok_state(*args, **kwargs):
        return make_state(volume=50)

    async def record_close():
        closed.append(True)

    coord.client.get_state = ok_state if state_ok else fail
    coord.client.get_metadata = fail
    coord.client.get_paired_bt_devices = fail
    coord.client.close = record_close
    return closed


@pytest.mark.asyncio
async def test_poll_failures_trigger_forced_close():
    """N подряд полностью неудачных циклов → client.close() (страховка от half-open)."""
    coord = build_coordinator(track=make_track(), state=make_state(volume=50))
    closed = _patch_client(coord)

    for i in range(POLL_FAILURES_BEFORE_RECONNECT - 1):
        await coord._refresh_state_and_track(notify=False)
        assert closed == [], f"close после {i + 1} циклов — рано"

    await coord._refresh_state_and_track(notify=False)
    assert closed == [True], "после порогового цикла должен быть принудительный close()"
    # счётчик сброшен — следующая серия отсчитывается заново
    assert coord._poll_failures == 0


@pytest.mark.asyncio
async def test_poll_failure_does_not_wipe_state():
    """Неудачный цикл не должен затирать последний известный state."""
    old = make_state(volume=50, muted=False)
    coord = build_coordinator(track=make_track(), state=old)
    _patch_client(coord)
    await coord._refresh_state_and_track(notify=False)
    assert coord.state is old


@pytest.mark.asyncio
async def test_successful_poll_resets_failure_counter():
    """Успешный цикл сбрасывает счётчик — редкие одиночные сбои не копятся до close()."""
    coord = build_coordinator(track=make_track(), state=make_state(volume=50))
    all_closed: list[bool] = []

    closed = _patch_client(coord)  # всё падает
    all_closed = closed
    await coord._refresh_state_and_track(notify=False)
    assert coord._poll_failures == 1

    _patch_client(coord, state_ok=True)  # get_state успешен → сброс
    await coord._refresh_state_and_track(notify=False)
    assert coord._poll_failures == 0

    closed2 = _patch_client(coord)  # снова всё падает — серия начинается с нуля
    await coord._refresh_state_and_track(notify=False)
    assert coord._poll_failures == 1
    assert all_closed == [] and closed2 == [], "close не должен вызываться при сброшенной серии"


# ─────────── регрессия: «таймлайн сбрасывается на 0 каждые 15 секунд» ───────────
#
# Прод-баг 0.14.x: poll-ответ get_metadata несёт позицию на момент ПОСЛЕДНЕГО
# события (стартовый снапшот трека), а не текущую. _stamp_track ставил свежий
# received_monotonic на этот stale-снапшот — экстраполяция track_position
# начиналась заново от старой позиции на каждом poll-цикле.

def _track_snapshot(**kw):
    from tests._fakes import make_track
    defaults = dict(position_sec=0, position_ts_ms=1_700_000_000_000, playing=True,
                    duration_sec=300)
    defaults.update(kw)
    return make_track(**defaults)


def test_stamp_track_keeps_base_for_same_position_snapshot(monkeypatch):
    """Тот же снапшот позиции (track_id+tsMs+position_sec) → база экстраполяции
    сохраняется, и track_position продолжает расти, а не откатывается к 0."""
    import time as time_mod

    from sboom_ha.helpers import track_position

    coord = build_coordinator(track=None, state=make_state())

    t0 = time_mod.monotonic()
    monkeypatch.setattr("sboom_ha.coordinator.time.monotonic", lambda: t0)
    first = coord._stamp_track(_track_snapshot())
    coord.track = first
    assert first.received_monotonic == t0

    # Через 15 секунд poll принёс ИДЕНТИЧНЫЙ снапшот (нового события не было)
    monkeypatch.setattr("sboom_ha.coordinator.time.monotonic", lambda: t0 + 15)
    polled = coord._stamp_track(_track_snapshot())
    coord.track = polled
    assert polled.received_monotonic == t0, "база экстраполяции должна переноситься"

    # Позиция во внешнем мире: ~15 c, а не 0
    monkeypatch.setattr("sboom_ha.helpers.time.monotonic", lambda: t0 + 15)
    pos = track_position(coord)
    assert pos is not None and 14.5 <= pos <= 15.5, f"позиция откатилась: {pos}"


def test_stamp_track_restamps_on_new_event(monkeypatch):
    """Новое событие на колонке (другой tsMs — seek/pause/resume) → свежий штамп."""
    import time as time_mod

    coord = build_coordinator(track=None, state=make_state())
    t0 = time_mod.monotonic()
    monkeypatch.setattr("sboom_ha.coordinator.time.monotonic", lambda: t0)
    coord.track = coord._stamp_track(_track_snapshot())

    monkeypatch.setattr("sboom_ha.coordinator.time.monotonic", lambda: t0 + 15)
    seeked = coord._stamp_track(
        _track_snapshot(position_sec=60, position_ts_ms=1_700_000_015_000)
    )
    assert seeked.received_monotonic == t0 + 15, "новый снапшот — новая база"


def test_stamp_track_restamps_on_track_change(monkeypatch):
    """Смена трека → свежий штамп даже при совпадающем position_sec."""
    import time as time_mod

    coord = build_coordinator(track=None, state=make_state())
    t0 = time_mod.monotonic()
    monkeypatch.setattr("sboom_ha.coordinator.time.monotonic", lambda: t0)
    coord.track = coord._stamp_track(_track_snapshot(track_id="1001"))

    monkeypatch.setattr("sboom_ha.coordinator.time.monotonic", lambda: t0 + 5)
    next_track = coord._stamp_track(_track_snapshot(track_id="2002"))
    assert next_track.received_monotonic == t0 + 5
