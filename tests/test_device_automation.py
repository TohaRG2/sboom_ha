"""Тесты device_trigger / device_action — обёртки над событиями и командами.

Проверяем контракт списков (все события/команды экспонированы) и dispatch
(action_type → правильная команда клиента). Полная привязка триггеров к шине —
интеграционная, требует HA-ядра; здесь — чистые функции и резолв устройства.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests._fakes import build_coordinator, make_state
from tests._ha_stubs import (
    DeviceEntry,
    HomeAssistant,
    _async_get_device_registry,
    install_stubs,
)

install_stubs()

from sboom_ha import device_action, device_trigger  # noqa: E402
from sboom_ha.const import DOMAIN  # noqa: E402
from sboom_ha.coordinator import (  # noqa: E402
    EVENT_CONNECTION_CHANGED,
    EVENT_TRACK_CHANGED,
    EVENT_VOLUME_CHANGED,
)

# ─────────────────── device_trigger ───────────────────

@pytest.mark.asyncio
async def test_triggers_expose_all_events():
    """Все 4 события колонки доступны как device-триггеры для устройства."""
    triggers = await device_trigger.async_get_triggers(HomeAssistant(), "ha-dev-1")
    types = {t["type"] for t in triggers}
    assert types == {"track_changed", "playback_changed", "volume_changed", "connection_changed"}
    assert all(t["device_id"] == "ha-dev-1" and t["domain"] == DOMAIN for t in triggers)


def test_trigger_type_maps_to_real_events():
    """Маппинг типов на реальные event_type из coordinator — не разъедется
    при переименовании события."""
    m = device_trigger.TRIGGER_TYPE_TO_EVENT
    assert m["track_changed"] == EVENT_TRACK_CHANGED
    assert m["volume_changed"] == EVENT_VOLUME_CHANGED
    assert m["connection_changed"] == EVENT_CONNECTION_CHANGED


def test_trigger_resolves_sber_device_id_from_identifiers():
    """Фильтр триггера строится по Sber device_id из identifiers устройства."""
    hass = HomeAssistant()
    reg = _async_get_device_registry(hass)
    reg.register(DeviceEntry(id="ha-dev-1", identifiers={(DOMAIN, "sber-xyz")}))
    assert device_trigger._sber_device_id(hass, "ha-dev-1") == "sber-xyz"
    # Неизвестное устройство → None (фильтр не навешивается, не падаем).
    assert device_trigger._sber_device_id(hass, "ghost") is None


# ─────────────────── device_action ───────────────────

@pytest.mark.asyncio
async def test_actions_expose_all_commands():
    actions = await device_action.async_get_actions(HomeAssistant(), "ha-dev-1")
    types = {a["type"] for a in actions}
    assert types == {"play", "pause", "next", "previous",
                     "refresh_metadata", "find_remote", "bt_pairing"}


def _hass_with_device(coord):
    """Регистрирует устройство колонки в registry + entry в config_entries."""
    hass = coord.hass
    reg = _async_get_device_registry(hass)
    reg.register(DeviceEntry(id="ha-dev-1", config_entries={coord.entry.entry_id}))
    return hass


@pytest.mark.asyncio
async def test_action_dispatches_media_command():
    """play → client.media_play(); каждое действие бьёт в свою команду."""
    coord = build_coordinator(state=make_state())
    coord.client.media_pause = AsyncMock()
    coord.client.media_next = AsyncMock()
    coord.client.find_remote = AsyncMock()
    hass = _hass_with_device(coord)

    await device_action.async_call_action_from_config(
        hass, {"device_id": "ha-dev-1", "type": "pause"}, {}, None)
    coord.client.media_pause.assert_awaited_once()

    await device_action.async_call_action_from_config(
        hass, {"device_id": "ha-dev-1", "type": "find_remote"}, {}, None)
    coord.client.find_remote.assert_awaited_once()
    coord.client.media_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_refresh_metadata_uses_coordinator():
    coord = build_coordinator(state=make_state())
    coord.async_request_refresh = AsyncMock()
    hass = _hass_with_device(coord)
    await device_action.async_call_action_from_config(
        hass, {"device_id": "ha-dev-1", "type": "refresh_metadata"}, {}, None)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_action_unknown_device_raises():
    from homeassistant.exceptions import InvalidDeviceAutomationConfig

    from tests._ha_stubs import install_stubs  # noqa: F401

    hass = HomeAssistant()
    with pytest.raises(InvalidDeviceAutomationConfig):
        await device_action.async_call_action_from_config(
            hass, {"device_id": "ghost", "type": "play"}, {}, None)
