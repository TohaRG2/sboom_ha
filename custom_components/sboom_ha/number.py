"""Number-entity — слайдер громкости."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .coordinator import SboomCoordinator

# Команды идут к колонке через единый WS с собственным lock — HA-параллелизм не нужен.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SboomCoordinator = entry.runtime_data
    async_add_entities([SboomVolumeNumber(coord, entry)])


class SboomVolumeNumber(SboomEntity, NumberEntity):
    _attr_translation_key = "volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    # Громкость доступна через media_player slider — не дублируем на dashboard.
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_volume"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.state.volume_percent if self.coordinator.state else None

    async def async_set_native_value(self, value: float) -> None:
        await self._run_command(
            self.coordinator.client.set_volume(int(value)), action="set volume"
        )
        await self.coordinator.async_request_refresh()
