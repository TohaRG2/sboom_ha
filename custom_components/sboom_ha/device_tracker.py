"""Device tracker — колонка сама себя позиционирует по Wi-Fi (location.*).

GET_STATE (op=12) содержит подсистему location {lat, lon, accuracy, source}
с source="wifi": колонка знает своё местоположение по Wi-Fi-геолокации.
Полезно для стационарных сценариев (зона «дома») и как якорь для карты.
Отключён по умолчанию — координаты устройства это чувствительные данные.
"""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .coordinator import SboomCoordinator

# Read-only, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    async_add_entities([SboomDeviceTracker(coordinator, entry)])


class SboomDeviceTracker(SboomEntity, TrackerEntity):
    """GPS-трекер колонки на основе её Wi-Fi-геолокации."""

    _attr_translation_key = "location"
    _attr_icon = "mdi:speaker-wireless"
    # Координаты — чувствительные данные, включается вручную.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_location"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        dev = self.device_state
        return dev.latitude if dev else None

    @property
    def longitude(self) -> float | None:
        dev = self.device_state
        return dev.longitude if dev else None

    @property
    def location_accuracy(self) -> int:
        # HA ждёт int (метры); отсутствие данных → 0 (HA трактует как «неизвестно»).
        dev = self.device_state
        return dev.location_accuracy if dev and dev.location_accuracy is not None else 0
