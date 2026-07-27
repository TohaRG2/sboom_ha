"""Тесты Options Flow + чтение опций в coordinator."""
from __future__ import annotations

from sboom_ha.const import (
    DEFAULT_AVAILABILITY_THRESHOLD,
    DEFAULT_KARAOKE_FILL,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_LYRICS_ENABLED,
    DEFAULT_VOLUME_POLL_INTERVAL,
    OPT_AVAILABILITY_THRESHOLD,
    OPT_KARAOKE_FILL,
    OPT_KEEPALIVE_INTERVAL,
    OPT_LYRICS_ENABLED,
    OPT_VOLUME_POLL_INTERVAL,
)
from sboom_ha.coordinator import SboomCoordinator

from tests._fakes import make_entry, make_state, make_track
from tests._ha_stubs import HomeAssistant


def _coord_with_options(**opts):
    entry = make_entry()
    entry.options = dict(opts)
    return SboomCoordinator(HomeAssistant(), entry)


# ─────────────────── defaults ───────────────────

def test_defaults_when_no_options():
    coord = _coord_with_options()
    assert coord._volume_poll_interval == DEFAULT_VOLUME_POLL_INTERVAL
    assert coord._keepalive_interval == DEFAULT_KEEPALIVE_INTERVAL
    assert coord._availability_threshold == DEFAULT_AVAILABILITY_THRESHOLD
    assert coord._lyrics_enabled == DEFAULT_LYRICS_ENABLED


def test_default_volume_interval_propagates_to_update_interval():
    coord = _coord_with_options()
    assert coord.update_interval.total_seconds() == DEFAULT_VOLUME_POLL_INTERVAL


# ─────────────────── overrides ───────────────────

def test_volume_poll_interval_override():
    coord = _coord_with_options(**{OPT_VOLUME_POLL_INTERVAL: 15})
    assert coord._volume_poll_interval == 15
    assert coord.update_interval.total_seconds() == 15


def test_keepalive_interval_override():
    coord = _coord_with_options(**{OPT_KEEPALIVE_INTERVAL: 60})
    assert coord._keepalive_interval == 60


def test_availability_threshold_override():
    coord = _coord_with_options(**{OPT_AVAILABILITY_THRESHOLD: 7})
    assert coord._availability_threshold == 7


def test_lyrics_enabled_override():
    coord = _coord_with_options(**{OPT_LYRICS_ENABLED: False})
    assert coord._lyrics_enabled is False


def test_karaoke_fill_defaults_off():
    """Закраска караоке по умолчанию выключена (статичный текст)."""
    assert DEFAULT_KARAOKE_FILL is False
    assert _coord_with_options().karaoke_fill is False


def test_karaoke_fill_override():
    assert _coord_with_options(**{OPT_KARAOKE_FILL: True}).karaoke_fill is True


# ─────────────────── lyrics_enabled gating ───────────────────

def test_maybe_fetch_lyrics_no_op_when_disabled():
    """Если lyrics отключены — _maybe_fetch_lyrics ничего не делает."""
    entry = make_entry()
    entry.options = {OPT_LYRICS_ENABLED: False}
    coord = SboomCoordinator(HomeAssistant(), entry)
    coord.track = make_track(track_id="X")  # валидный трек
    coord._maybe_fetch_lyrics()
    # Если lyrics disabled — track_id НЕ должен попасть в inflight set
    assert "X" not in coord.lyrics._inflight


def test_maybe_fetch_lyrics_runs_when_enabled():
    """Если lyrics включены — fetch инициируется (track в inflight)."""
    entry = make_entry()
    entry.options = {OPT_LYRICS_ENABLED: True}
    coord = SboomCoordinator(HomeAssistant(), entry)
    coord.track = make_track(track_id="Y", title="t", artists=["a"])
    coord._maybe_fetch_lyrics()
    # Background task создан → track_id в inflight
    assert "Y" in coord.lyrics._inflight


# ─────────────────── coexistence with state ───────────────────

def test_options_dont_break_existing_coordinator_methods():
    """Sanity: с опциями всё что было — работает."""
    entry = make_entry()
    entry.options = {OPT_VOLUME_POLL_INTERVAL: 10, OPT_KEEPALIVE_INTERVAL: 30}
    coord = SboomCoordinator(HomeAssistant(), entry)
    coord.state = make_state(volume=42)
    coord.track = make_track()
    coord._set_connected(True)
    assert coord.connected is True
    assert coord.state.volume_percent == 42
