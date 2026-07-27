"""Binary-сенсоры sboom_ha: булевы подсистемы из GET_STATE (декларативно)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from ._models import DeviceState
from .coordinator import SboomCoordinator

# Read-only сенсоры, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SboomBinarySensorSpec:
    """Декларативное описание binary-сенсора (паттерн как в button.py/switch.py)."""

    key: str  # суффикс unique_id
    translation_key: str
    icon: str | None = None
    is_on_fn: Callable[[SboomCoordinator], bool | None]
    attrs_fn: Callable[[SboomCoordinator], dict | None] | None = None
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None
    enabled_default: bool = True


def _dev(c: SboomCoordinator) -> DeviceState | None:
    """Подсистемы устройства из последнего GET_STATE, либо None."""
    return c.state.device if c.state else None


BINARY_SENSOR_SPECS: tuple[SboomBinarySensorSpec, ...] = (
    # LED-дисплей колонки включён.
    SboomBinarySensorSpec(
        key="led_display",
        translation_key="led_display",
        icon="mdi:television-ambient-light",
        is_on_fn=lambda c: dev.led_on if (dev := _dev(c)) else None,
    ),
    # Колонка активна (не в режиме сна).
    SboomBinarySensorSpec(
        key="active",
        translation_key="active",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda c: (
            None
            if (dev := _dev(c)) is None or dev.sleep_state is None
            else dev.sleep_state == "working"
        ),
    ),
    # Колонка работает в стереопаре.
    SboomBinarySensorSpec(
        key="stereo_pair",
        translation_key="stereo_pair",
        icon="mdi:speaker-multiple",
        is_on_fn=lambda c: dev.stereo_pair_active if (dev := _dev(c)) else None,
    ),
    # Устройство привязано к подписке.
    SboomBinarySensorSpec(
        key="subscription_device",
        translation_key="subscription_device",
        icon="mdi:card-account-details",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: dev.is_subscription_device if (dev := _dev(c)) else None,
    ),
    # Режим домашней безопасности включён.
    SboomBinarySensorSpec(
        key="home_security",
        translation_key="home_security",
        icon="mdi:shield-home",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: dev.home_security if (dev := _dev(c)) else None,
    ),
    # Идёт «утреннее шоу»; from_show (запущено из шоу) — в атрибутах.
    SboomBinarySensorSpec(
        key="morning_show",
        translation_key="morning_show",
        icon="mdi:weather-sunset-up",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: dev.in_morning_show if (dev := _dev(c)) else None,
        attrs_fn=lambda c: (
            {"from_show": dev.morning_show_from}
            if (dev := _dev(c)) and dev.morning_show_from is not None
            else None
        ),
    ),
    # Будильник/таймер звонит прямо сейчас — идеальный триггер для сценариев
    # пробуждения (alarm.playing из GET_STATE).
    SboomBinarySensorSpec(
        key="alarm_ringing",
        translation_key="alarm_ringing",
        device_class=BinarySensorDeviceClass.SOUND,
        icon="mdi:alarm-bell",
        is_on_fn=lambda c: dev.alarm_ringing if (dev := _dev(c)) else None,
    ),
    # У текущего трека есть текст на стороне Sber (info.hasLyrics). Источник —
    # track, а не device_state.
    SboomBinarySensorSpec(
        key="track_has_lyrics",
        translation_key="track_has_lyrics",
        icon="mdi:script-text-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: c.track.has_lyrics if c.track else None,
    ),
    # Автогромкость голосового ассистента включена (assistant.auto_volume).
    SboomBinarySensorSpec(
        key="assistant_auto_volume",
        translation_key="assistant_auto_volume",
        icon="mdi:volume-vibrate",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: dev.assistant_auto_volume if (dev := _dev(c)) else None,
    ),
    # Ассистент хочет проактивно что-то сообщить (proactivityNotification).
    SboomBinarySensorSpec(
        key="proactivity_notification",
        translation_key="proactivity_notification",
        icon="mdi:message-badge",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: dev.proactivity_notification if (dev := _dev(c)) else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    async_add_entities(
        SboomDeviceBinarySensor(coordinator, entry, spec)
        for spec in BINARY_SENSOR_SPECS
    )


class SboomDeviceBinarySensor(SboomEntity, BinarySensorEntity):
    """Generic binary-сенсор, полностью описанный через SboomBinarySensorSpec."""

    def __init__(
        self,
        coordinator: SboomCoordinator,
        entry: ConfigEntry,
        spec: SboomBinarySensorSpec,
    ) -> None:
        super().__init__(coordinator, entry)
        self._spec = spec
        self._attr_unique_id = f"{self._device_unique_prefix}_{spec.key}"
        self._attr_translation_key = spec.translation_key
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        self._attr_entity_registry_enabled_default = spec.enabled_default

    @property
    def is_on(self) -> bool | None:
        return self._spec.is_on_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._spec.attrs_fn is None:
            return None
        return self._spec.attrs_fn(self.coordinator)
