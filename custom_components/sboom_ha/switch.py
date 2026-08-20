"""Switch-entities: shuffle, mute."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .coordinator import SboomCoordinator

# Команды идут к колонке через единый WS с собственным lock — HA-параллелизм не нужен.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SboomSwitch(SwitchEntityDescription):
    is_on_fn: Callable[[SboomCoordinator], bool | None]
    turn_on_fn: Callable[[SboomCoordinator], Awaitable[None]]
    turn_off_fn: Callable[[SboomCoordinator], Awaitable[None]]


# Команды идут через единый слой coordinator.async_execute (аудит #18):
# optimistic-патч и refresh-политика там — переключатели не «отпрыгивают».
SWITCHES: tuple[SboomSwitch, ...] = (
    SboomSwitch(
        key="shuffle",
        translation_key="shuffle",
        icon="mdi:shuffle-variant",
        is_on_fn=lambda c: c.track.shuffle if c.track else None,
        turn_on_fn=lambda c: c.async_execute("shuffle", True),
        turn_off_fn=lambda c: c.async_execute("shuffle", False),
    ),
    SboomSwitch(
        key="mute",
        translation_key="mute",
        icon="mdi:volume-mute",
        is_on_fn=lambda c: c.state.muted if c.state else None,
        turn_on_fn=lambda c: c.async_execute("mute"),
        turn_off_fn=lambda c: c.async_execute("unmute"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SboomCoordinator = entry.runtime_data
    async_add_entities(SboomSwitchEntity(coord, entry, d) for d in SWITCHES)


class SboomSwitchEntity(SboomEntity, SwitchEntity):
    # Дублируют возможности media_player (shuffle/mute) — скрыты по умолчанию.
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry, desc: SboomSwitch) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = desc
        self._attr_unique_id = f"{self._device_unique_prefix}_{desc.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator)

    async def async_turn_on(self, **kwargs) -> None:
        await self._run_command(
            self.entity_description.turn_on_fn(self.coordinator),
            action=f"{self.entity_description.key} on",
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self._run_command(
            self.entity_description.turn_off_fn(self.coordinator),
            action=f"{self.entity_description.key} off",
        )
