"""Тесты event bus в SboomCoordinator: track/playback/volume changes."""
from __future__ import annotations

import pytest
from sboom_ha.const import OP_GET_STATE
from sboom_ha.coordinator import (
    EVENT_CONNECTION_CHANGED,
    EVENT_PLAYBACK_CHANGED,
    EVENT_TRACK_CHANGED,
    EVENT_VOLUME_CHANGED,
)

from tests._fakes import build_coordinator, make_state, make_track


def _fire(coord, event_type: str, count: int = 1):
    """Помощник: проверяет что в bus.fired есть N событий с заданным типом, возвращает payload'ы."""
    found = [data for et, data in coord.hass.bus.fired if et == event_type]
    assert len(found) == count, (
        f"expected {count} `{event_type}` events, got {len(found)}; "
        f"fired={coord.hass.bus.fired}"
    )
    return found


# ─────────────────────── _fire_change_events напрямую ───────────────────────

def test_track_changed_fires_on_new_track():
    coord = build_coordinator(track=make_track(track_id="A"), state=make_state())
    coord.hass.bus.fired.clear()
    coord.track = make_track(track_id="B", title="New", artists=["X"], album="Y")
    coord._fire_change_events(prev_track=make_track(track_id="A"), prev_state=coord.state)

    [payload] = _fire(coord, EVENT_TRACK_CHANGED)
    assert payload["track_id"] == "B"
    assert payload["title"] == "New"
    assert payload["artists"] == ["X"]
    assert payload["album"] == "Y"
    assert payload["previous_track_id"] == "A"
    assert payload["entry_id"] == coord.entry.entry_id
    assert payload["host"] == "192.0.2.10"


def test_track_changed_fires_when_initial_track_appears():
    """prev=None, current=валидный трек — это тоже событие смены."""
    coord = build_coordinator(track=make_track(track_id="A"), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(prev_track=None, prev_state=coord.state)

    [payload] = _fire(coord, EVENT_TRACK_CHANGED)
    assert payload["track_id"] == "A"
    assert payload["previous_track_id"] is None


def test_no_track_event_when_track_id_unchanged_and_no_other_changes():
    coord = build_coordinator(track=make_track(track_id="A", playing=True), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=make_track(track_id="A", playing=True),
        prev_state=coord.state,
    )
    assert coord.hass.bus.fired == []


def test_playback_changed_fires_when_only_playing_flips():
    coord = build_coordinator(track=make_track(track_id="A", playing=False), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=make_track(track_id="A", playing=True),
        prev_state=coord.state,
    )
    [payload] = _fire(coord, EVENT_PLAYBACK_CHANGED)
    assert payload["playing"] is False
    assert payload["track_id"] == "A"
    # Не должно быть track_changed для того же track_id
    assert all(et != EVENT_TRACK_CHANGED for et, _ in coord.hass.bus.fired)


def test_playback_changed_fires_on_shuffle_or_repeat_change():
    coord = build_coordinator(
        track=make_track(track_id="A", shuffle=True, repeat="track"),
        state=make_state(),
    )
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=make_track(track_id="A", shuffle=False, repeat="none"),
        prev_state=coord.state,
    )
    [payload] = _fire(coord, EVENT_PLAYBACK_CHANGED)
    assert payload["shuffle"] is True
    assert payload["repeat"] == "track"


def test_volume_changed_fires_on_volume_diff():
    coord = build_coordinator(track=make_track(), state=make_state(volume=70, muted=False))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=coord.track,
        prev_state=make_state(volume=50, muted=False),
    )
    [payload] = _fire(coord, EVENT_VOLUME_CHANGED)
    assert payload["volume_percent"] == 70
    assert payload["muted"] is False


def test_volume_changed_fires_on_mute_toggle():
    coord = build_coordinator(track=make_track(), state=make_state(volume=50, muted=True))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=coord.track,
        prev_state=make_state(volume=50, muted=False),
    )
    [payload] = _fire(coord, EVENT_VOLUME_CHANGED)
    assert payload["muted"] is True


def test_volume_changed_fires_when_initial_state_appears():
    coord = build_coordinator(track=make_track(), state=make_state(volume=33, muted=False))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(prev_track=coord.track, prev_state=None)
    [payload] = _fire(coord, EVENT_VOLUME_CHANGED)
    assert payload["volume_percent"] == 33


def test_no_volume_event_when_state_unchanged():
    coord = build_coordinator(track=make_track(), state=make_state(volume=50, muted=False))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=coord.track,
        prev_state=make_state(volume=50, muted=False),
    )
    assert all(et != EVENT_VOLUME_CHANGED for et, _ in coord.hass.bus.fired)


def test_event_payload_includes_device_context():
    """Каждое событие должно содержать entry_id, device_id, host."""
    coord = build_coordinator(track=make_track(track_id="A"), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(prev_track=None, prev_state=None)
    for _, payload in coord.hass.bus.fired:
        assert "entry_id" in payload
        assert "device_id" in payload
        assert "host" in payload


# ─────────────────────── _handle_event end-to-end ───────────────────────

def _push_track_payload(track_id: str, title: str = "T") -> tuple[bytes, dict]:
    """Сборка валидного push-payload + parsed-обёртки для _handle_event."""
    raw = (
        b'\x00\x00{"artists":[{"id":"1","name":"A"}],'
        + b'"playing":true,"position":{"tsMs":1700000000000,"val":10},'
        + b'"provider":"zvuk","releases":[{"id":"5","name":"R"}],'
        + b'"repeatType":"none","shuffle":false,'
        + f'"title":"{title}","trackId":"{track_id}"'.encode()
        + b'}'
    )
    parsed = {5: {10: True}}  # field 10 = metadata-update маркер
    return raw, parsed


@pytest.mark.asyncio
async def test_handle_event_metadata_push_fires_track_changed():
    coord = build_coordinator(track=None, state=make_state())
    coord.hass.bus.fired.clear()
    raw, parsed = _push_track_payload("999", title="First")
    await coord._handle_event(raw, parsed)

    [payload] = _fire(coord, EVENT_TRACK_CHANGED)
    assert payload["track_id"] == "999"
    assert payload["title"] == "First"


@pytest.mark.asyncio
async def test_handle_event_idempotent_when_same_track():
    coord = build_coordinator(track=None, state=make_state())
    raw, parsed = _push_track_payload("777")
    await coord._handle_event(raw, parsed)  # first push: track A → fire
    coord.hass.bus.fired.clear()
    await coord._handle_event(raw, parsed)  # second push: still A → no fire
    assert all(et != EVENT_TRACK_CHANGED for et, _ in coord.hass.bus.fired)


@pytest.mark.asyncio
async def test_handle_event_unparseable_state_push_keeps_state():
    """Регрессия: state-push с нераспознанным payload не должен затирать self.state."""
    old = make_state(volume=42, muted=False)
    coord = build_coordinator(track=make_track(), state=old)
    coord.hass.bus.fired.clear()
    # Маркер state-update стоит, но payload — мусор: parse_state вернёт None.
    parsed = {5: {OP_GET_STATE: True}}
    await coord._handle_event(b"\x00\x00garbage, no json here\x00", parsed)
    assert coord.state is old, "битый push не должен трогать последний известный state"
    # changed=False → никакие события/данные не публикуются
    assert coord.hass.bus.fired == []


@pytest.mark.asyncio
async def test_handle_event_metadata_push_stamps_received_times():
    """Push metadata обновляет track и ставит штампы времени получения.

    Без received_monotonic ломается экстраполяция позиции (helpers),
    без received_ts — media_position_updated_at."""
    coord = build_coordinator(track=None, state=make_state())
    raw, parsed = _push_track_payload("321", title="Stamped")
    await coord._handle_event(raw, parsed)
    assert coord.track is not None
    assert coord.track.track_id == "321"
    assert coord.track.received_monotonic is not None
    assert coord.track.received_ts is not None


# ─────────────────────── _set_connected при unload ───────────────────────

def test_set_connected_suppressed_while_stopping():
    """Регрессия: штатный unload (stopping) стрелял connection_changed и будил listeners."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.connected = True
    coord._stopping = True
    coord.hass.bus.fired.clear()
    listeners_before = getattr(coord, "_listener_calls", 0)

    coord._set_connected(False)

    assert coord.connected is False  # флаг всё же обновлён
    assert all(et != EVENT_CONNECTION_CHANGED for et, _ in coord.hass.bus.fired), (
        "при _stopping=True событие в bus идти не должно"
    )
    assert getattr(coord, "_listener_calls", 0) == listeners_before, (
        "при _stopping=True async_update_listeners не должен вызываться"
    )


def test_set_connected_fires_event_when_not_stopping():
    """Контраст: при рабочем состоянии переход connected→False публикуется."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.connected = True
    coord._stopping = False
    coord.hass.bus.fired.clear()
    listeners_before = getattr(coord, "_listener_calls", 0)

    coord._set_connected(False)

    [payload] = _fire(coord, EVENT_CONNECTION_CHANGED)
    assert payload["connected"] is False
    assert getattr(coord, "_listener_calls", 0) == listeners_before + 1
