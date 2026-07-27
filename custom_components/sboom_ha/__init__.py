"""SBoom (LAN) — Home Assistant custom integration for SberBoom-class speakers."""
from __future__ import annotations

import logging
import pathlib
from typing import Any

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import (
    DEFAULT_PANEL_ENABLED,
    DOMAIN,
    OPT_PANEL_ENABLED,
    PANEL_STATIC_PATH,
    PANEL_URL_PATH,
)
from .coordinator import SboomCoordinator
from .services import async_register_services
from .websocket_api import async_setup_websocket_api

_LOGGER = logging.getLogger(__name__)

# Координатор живёт в entry.runtime_data (IQS bronze runtime-data),
# а не в hass.data[DOMAIN].
SboomConfigEntry = ConfigEntry  # alias для читаемости сигнатур

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.CALENDAR,
]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Регистрация сервисов на старте HA (IQS bronze action-setup).

    Сервисы должны существовать даже когда ни один entry не загружен —
    тогда автоматизации с ними валидируются и падают с внятной ошибкой
    (ServiceValidationError), а не с «service not found».
    """
    async_register_services(hass)
    async_setup_websocket_api(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SboomConfigEntry) -> bool:
    """Поднимаем coordinator + форвардим платформы."""
    coordinator = SboomCoordinator(hass, entry)
    await coordinator.async_start()

    entry.runtime_data = coordinator

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Иначе утекли бы supervisor-task и открытый WS: HA повторит setup,
        # а старый координатор продолжил бы жить без владельца.
        await coordinator.async_stop()
        raise
    await _async_register_panel(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_register_panel(
    hass: HomeAssistant, entry: SboomConfigEntry
) -> None:
    """Раздать www/ и (опционально) зарегистрировать built-in panel.

    **Статика `/sboom_panel` раздаётся ВСЕГДА**, независимо от опции
    `panel_enabled`: Lovelace-карточка `sboom-card` импортирует общие
    компоненты из `/sboom_panel/…` в рантайме и должна работать даже при
    отключённой боковой панели. Опцией `panel_enabled` (Settings →
    Integrations → SBoom → Configure) управляется только пункт в боковом меню.
    """
    # 1. Статика www/ — один раз на HA, вне зависимости от panel_enabled.
    static_marker = f"{DOMAIN}_static_registered"
    if not hass.data.get(static_marker):
        panel_dir = str(pathlib.Path(__file__).parent / "www")
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_PATH, panel_dir, cache_headers=False)]
        )
        # Версия из manifest → cache-buster JS меняется вместе с версией.
        integration = await async_get_integration(hass, DOMAIN)
        hass.data[f"{DOMAIN}_version"] = integration.version or "0"
        hass.data[static_marker] = True

    # 2. Боковая панель — по опции panel_enabled.
    marker = f"{DOMAIN}_panel_registered"
    if not entry.options.get(OPT_PANEL_ENABLED, DEFAULT_PANEL_ENABLED):
        if hass.data.pop(marker, None):
            async_remove_panel(hass, PANEL_URL_PATH)
        return
    if hass.data.get(marker):
        return

    version = hass.data[f"{DOMAIN}_version"]
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="SberBoom",
        sidebar_icon="mdi:speaker",
        frontend_url_path=PANEL_URL_PATH,
        config={
            # version прокидывается в панель через this.panel.config.version.
            "version": version,
            "_panel_custom": {
                "name": "sboom-panel",
                "module_url": f"{PANEL_STATIC_PATH}/sboom-panel.js?v={version}",
            },
        },
        require_admin=False,
    )
    hass.data[marker] = True


async def async_unload_entry(hass: HomeAssistant, entry: SboomConfigEntry) -> bool:
    """Очистка при удалении.

    Порядок стандартный для HA: сначала выгружаем платформы (их entities ещё
    могут обращаться к координатору), и только при успехе гасим координатор.
    Иначе неудачная выгрузка платформ оставила бы entry в состоянии loaded
    с уже остановленным координатором.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: SboomCoordinator | None = getattr(entry, "runtime_data", None)
        if coordinator is not None:
            await coordinator.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Полное удаление entry: чистим персистентный lyrics-кеш из .storage.

    Без этого файлы sboom_ha_lyrics_<entry_id> оставались бы сиротами.
    """
    from homeassistant.helpers.storage import Store

    from .lyrics_manager import LYRICS_STORE_VERSION

    store = Store(hass, LYRICS_STORE_VERSION, f"{DOMAIN}_lyrics_{entry.entry_id}")
    await store.async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Миграция config entry между MAJOR.MINOR версиями.

    Сейчас актуальная версия: VERSION=1, MINOR_VERSION=1 — миграции не требуются.
    При появлении изменения формата (data → options перенос или добавление полей)
    добавлять блоки `if version == 1: data = {...}; entry.minor_version = 2`.
    """
    _LOGGER.debug(
        "migrating config entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )
    # Будущие миграции — здесь.
    return True
