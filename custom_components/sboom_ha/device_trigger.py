"""Device triggers — UI-обёртка над событиями колонки в HA bus.

Интеграция уже публикует sboom_track_changed / sboom_playback_changed /
sboom_volume_changed / sboom_connection_changed. Здесь они превращаются в
device-триггеры: в конструкторе автоматизаций появляется «Устройство:
SberBoom → Сменился трек» без ручного event-триггера с YAML. Чистый
HA-слой, нового протокола не требует.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import (
    EVENT_CONNECTION_CHANGED,
    EVENT_PLAYBACK_CHANGED,
    EVENT_TRACK_CHANGED,
    EVENT_VOLUME_CHANGED,
)

# trigger_type (в UI/translations) → event_type в HA bus.
TRIGGER_TYPE_TO_EVENT = {
    "track_changed": EVENT_TRACK_CHANGED,
    "playback_changed": EVENT_PLAYBACK_CHANGED,
    "volume_changed": EVENT_VOLUME_CHANGED,
    "connection_changed": EVENT_CONNECTION_CHANGED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPE_TO_EVENT)}
)


def _sber_device_id(hass: HomeAssistant, ha_device_id: str) -> str | None:
    """HA registry device_id → Sber device_id (из identifiers).

    События несут Sber device_id (CONF_DEVICE_ID), поэтому фильтр триггера
    строится по нему, а не по HA-registry id.
    """
    device = dr.async_get(hass).async_get(ha_device_id)
    if device is None:
        return None
    for domain, ident in device.identifiers:
        if domain == DOMAIN:
            return ident
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Список device-триггеров для устройства колонки."""
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_TYPE_TO_EVENT
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Привязать device-триггер к соответствующему событию, фильтруя по колонке."""
    event_type = TRIGGER_TYPE_TO_EVENT[config[CONF_TYPE]]
    event_data = {}
    sber_id = _sber_device_id(hass, config[CONF_DEVICE_ID])
    if sber_id is not None:
        # Фильтруем по конкретной колонке — иначе триггер срабатывал бы на
        # события всех настроенных устройств.
        event_data["device_id"] = sber_id
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: event_type,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
