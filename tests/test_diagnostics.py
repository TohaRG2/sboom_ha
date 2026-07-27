"""Тесты diagnostics: дамп state + redaction чувствительных полей."""
from __future__ import annotations

import pytest
from sboom_ha.const import DOMAIN
from sboom_ha.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)

from tests._fakes import build_coordinator, make_state, make_track
from tests._ha_stubs import HomeAssistant


def _setup_hass_with_coord(coord) -> HomeAssistant:
    """Кладёт coordinator в hass.data[DOMAIN][entry_id], как в реальном setup."""
    hass = coord.hass
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    return hass


# ─────────────────────── happy path ───────────────────────

@pytest.mark.asyncio
async def test_config_entry_diagnostics_returns_full_payload():
    coord = build_coordinator(track=make_track(track_id="42"), state=make_state(volume=66))
    coord._set_connected(True)
    hass = _setup_hass_with_coord(coord)

    payload = await async_get_config_entry_diagnostics(hass, coord.entry)

    assert "entry" in payload
    assert payload["entry"]["version"] == 1
    assert payload["entry"]["minor_version"] == 1
    assert payload["coordinator"]["connected"] is True
    assert payload["coordinator"]["update_interval_sec"] == 15.0
    assert payload["track"]["track_id"] == "42"
    assert payload["state"]["volume_percent"] == 66


@pytest.mark.asyncio
async def test_device_diagnostics_returns_same_shape():
    """device-level diagnostics возвращают тот же payload (single-device интеграция)."""
    coord = build_coordinator(track=make_track(), state=make_state())
    hass = _setup_hass_with_coord(coord)

    from tests._ha_stubs import DeviceEntry
    payload = await async_get_device_diagnostics(hass, coord.entry, DeviceEntry())
    assert "entry" in payload
    assert "coordinator" in payload


# ─────────────────────── redaction ───────────────────────

@pytest.mark.asyncio
async def test_pin_token_redacted():
    coord = build_coordinator(track=make_track(), state=make_state())
    hass = _setup_hass_with_coord(coord)

    payload = await async_get_config_entry_diagnostics(hass, coord.entry)

    assert payload["entry"]["data"]["pin_access_token"] == "**REDACTED**"
    assert "test-pin-token" not in str(payload)  # никаких следов в любом поле


@pytest.mark.asyncio
async def test_host_redacted():
    coord = build_coordinator(track=make_track(), state=make_state())
    hass = _setup_hass_with_coord(coord)

    payload = await async_get_config_entry_diagnostics(hass, coord.entry)

    assert payload["entry"]["data"]["host"] == "**REDACTED**"
    assert "192.0.2.10" not in str(payload["entry"])


@pytest.mark.asyncio
async def test_serial_redacted():
    coord = build_coordinator(track=make_track(), state=make_state())
    hass = _setup_hass_with_coord(coord)

    payload = await async_get_config_entry_diagnostics(hass, coord.entry)

    assert payload["entry"]["data"]["device_id"] == "**REDACTED**"


@pytest.mark.asyncio
async def test_redact_set_covers_all_known_secrets():
    """Регрессия: список TO_REDACT не должен случайно потерять чувствительный ключ."""
    must_have = {"pin_access_token", "host", "device_id", "serial_number", "client_id"}
    assert must_have <= TO_REDACT


# ─────────────────────── edge cases ───────────────────────

@pytest.mark.asyncio
async def test_diagnostics_when_no_track_or_state():
    coord = build_coordinator(track=None, state=None)
    hass = _setup_hass_with_coord(coord)

    payload = await async_get_config_entry_diagnostics(hass, coord.entry)

    assert payload["track"] is None
    assert payload["state"] is None
    # coordinator-snapshot всё равно есть
    assert payload["coordinator"]["connected"] is False


@pytest.mark.asyncio
async def test_diagnostics_when_coordinator_not_in_hass_data():
    """Граничный кейс: hass.data пуст (entry не setup'ed)."""
    from sboom_ha.diagnostics import async_get_config_entry_diagnostics

    from tests._fakes import make_entry

    hass = HomeAssistant()
    entry = make_entry()
    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["coordinator"] is None
    assert payload["track"] is None
    assert payload["state"] is None
    # entry.data всё ещё есть (с redaction)
    assert payload["entry"]["data"]["pin_access_token"] == "**REDACTED**"
