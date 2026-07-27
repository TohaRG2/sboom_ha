"""Select-entity — режим повтора и скорость воспроизведения."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .const import PLAYBACK_SPEED_OPTIONS, REPEAT_TO_CANONICAL
from .coordinator import SboomCoordinator

# Команды идут к колонке через единый WS с собственным lock — HA-параллелизм не нужен.
PARALLEL_UPDATES = 0

REPEAT_OPTIONS = ["off", "playlist", "track"]
# Канон REPEAT_TO_CANONICAL → исторические опции этого select
# (менять labels нельзя — сломаются история state и переводы).
_CANON_TO_OPTION = {"off": "off", "all": "playlist", "one": "track"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SboomCoordinator = entry.runtime_data
    async_add_entities([
        SboomRepeatSelect(coord, entry),
        SboomPlaybackSpeedSelect(coord, entry),
    ])


class SboomRepeatSelect(SboomEntity, SelectEntity):
    _attr_translation_key = "repeat"
    _attr_icon = "mdi:repeat"
    _attr_options = REPEAT_OPTIONS
    # Repeat доступен через media_player.repeat — на dashboard не дублируем.
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_repeat"

    @property
    def current_option(self) -> str | None:
        track = self.coordinator.track
        if not track or not track.repeat:
            return None
        canon = REPEAT_TO_CANONICAL.get(track.repeat.lower())
        return _CANON_TO_OPTION.get(canon) if canon else None

    async def async_select_option(self, option: str) -> None:
        await self._run_command(
            self.coordinator.client.media_repeat(option), action="set repeat"
        )
        await self.coordinator.async_request_refresh()


class SboomPlaybackSpeedSelect(SboomEntity, SelectEntity):
    """Скорость воспроизведения трека (op=23, float-кодировка)."""

    _attr_translation_key = "playback_speed"
    _attr_icon = "mdi:play-speed"
    _attr_options = PLAYBACK_SPEED_OPTIONS
    # Side-feature — не дублируем на dashboard "Auto" (см. инвариант #7 в CLAUDE.md).
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_playback_speed"

    @property
    def current_option(self) -> str | None:
        track = self.coordinator.track
        if not track or track.playback_speed is None:
            return None
        # Сопоставляем float из метаданных с ближайшим пресетом.
        for opt in PLAYBACK_SPEED_OPTIONS:
            if abs(float(opt) - track.playback_speed) < 0.01:
                return opt
        return None

    async def async_select_option(self, option: str) -> None:
        await self._run_command(
            self.coordinator.client.set_playback_speed(float(option)),
            action="set playback speed",
        )
        await self.coordinator.async_request_refresh()
