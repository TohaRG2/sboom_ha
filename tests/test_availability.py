"""Тесты доступности entity при разрыве WS-сессии с колонкой."""
from __future__ import annotations

from sboom_ha.coordinator import EVENT_CONNECTION_CHANGED
from sboom_ha.media_player import SboomMediaPlayer

from tests._fakes import build_coordinator, make_state, make_track

# ─────────────────── available ───────────────────

def test_available_false_initially():
    """До первого успешного connect — coordinator.connected=False, entity недоступна."""
    coord = build_coordinator(track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert coord.connected is False
    assert mp.available is False


def test_available_true_after_set_connected():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.available is True


def test_available_flips_back_to_false_on_disconnect():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.available is True
    coord._set_connected(False)
    assert mp.available is False


# ─────────────────── _set_connected: events ───────────────────

def test_set_connected_fires_event_on_change():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.hass.bus.fired.clear()
    coord._set_connected(True)

    fired = [data for et, data in coord.hass.bus.fired if et == EVENT_CONNECTION_CHANGED]
    assert len(fired) == 1
    payload = fired[0]
    assert payload["connected"] is True
    assert payload["host"] == "192.0.2.10"
    assert "entry_id" in payload
    assert "device_id" in payload


def test_set_connected_does_not_fire_when_state_unchanged():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)  # initial fire
    coord.hass.bus.fired.clear()
    coord._set_connected(True)  # same value — no fire
    assert all(et != EVENT_CONNECTION_CHANGED for et, _ in coord.hass.bus.fired)


def test_set_connected_fires_on_each_transition():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.hass.bus.fired.clear()
    coord._set_connected(True)
    coord._set_connected(False)
    coord._set_connected(True)
    fired = [data for et, data in coord.hass.bus.fired if et == EVENT_CONNECTION_CHANGED]
    assert [p["connected"] for p in fired] == [True, False, True]


def test_set_connected_notifies_listeners():
    """async_update_listeners должен дёрнуться при смене флага — UI перерисуется."""
    coord = build_coordinator(track=make_track(), state=make_state())
    initial_calls = getattr(coord, "_listener_calls", 0)
    coord._set_connected(True)
    assert getattr(coord, "_listener_calls", 0) > initial_calls
