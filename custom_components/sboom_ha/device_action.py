"""Device actions — команды колонки в конструкторе автоматизаций.

«Устройство: SberBoom → Пауза / Следующий трек / Найти пульт …» без выбора
конкретной entity. Действия ходят через тот же координатор/клиент, что и
entity-команды; нового протокола не требуют.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_ACTION_BASE_SCHEMA
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import InvalidDeviceAutomationConfig
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SboomCoordinator

# action_type → как выполнить на координаторе. Медиа-дубли (play/pause/next/
# prev) удобны в UI; уникальные refresh/find_remote/bt_pairing — то, чего нет
# у стандартных media_player-действий.
ACTION_TYPES = (
    "play",
    "pause",
    "next",
    "previous",
    "refresh_metadata",
    "find_remote",
    "bt_pairing",
)

ACTION_SCHEMA = DEVICE_ACTION_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(ACTION_TYPES)}
)


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Список device-действий для устройства колонки."""
    return [
        {
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: action_type,
        }
        for action_type in ACTION_TYPES
    ]


def _coordinator(hass: HomeAssistant, ha_device_id: str) -> SboomCoordinator:
    """Координатор колонки по HA registry device_id (через её config entry)."""
    device = dr.async_get(hass).async_get(ha_device_id)
    if device is not None:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            coordinator = getattr(entry, "runtime_data", None) if entry else None
            if coordinator is not None:
                return coordinator
    raise InvalidDeviceAutomationConfig(
        f"SBoom device {ha_device_id} not found or not loaded"
    )


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: dict[str, Any],
    context: Context | None,
) -> None:
    """Выполнить device-действие."""
    coordinator = _coordinator(hass, config[CONF_DEVICE_ID])
    client = coordinator.client
    action = config[CONF_TYPE]

    if action == "refresh_metadata":
        await coordinator.async_request_refresh()
        return

    coro = {
        "play": client.media_play,
        "pause": client.media_pause,
        "next": client.media_next,
        "previous": client.media_prev,
        "find_remote": client.find_remote,
        "bt_pairing": client.bt_make_discoverable,
    }[action]
    await coro()
