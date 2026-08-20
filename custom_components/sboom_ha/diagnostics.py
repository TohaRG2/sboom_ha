"""Diagnostics для bug reports.

Settings → Devices → SBoom → ⋮ → Download diagnostics → JSON-файл.
Содержит state coordinator + последний trackInfo + entry.data с редактированием
чувствительных полей (pin-токен, host, serial).
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .coordinator import SboomCoordinator

# Поля entry.data + state, которые надо вырезать перед публикацией:
# - токен — даёт control над колонкой
# - host/serial/device_id — PII (идентификация устройства/сети пользователя)
# - client_id — UUID клиента, может коррелировать запросы
# - latitude/longitude/location_accuracy — точные координаты дома
# - network_ip — адрес в LAN пользователя
# - raw_state_json/raw — сырые payload'ы колонки: содержат все поля выше
#   строкой, куда async_redact_data не заглядывает
TO_REDACT = {
    "pin_access_token",
    "client_id",
    "device_id",
    "host",
    "client_host",  # coordinator snapshot — тот же PII, что и host
    "serial",
    "serial_number",
    "latitude",
    "longitude",
    "location_accuracy",
    "network_ip",
    "raw_state_json",
    "raw",
}


def _safe_dataclass(obj: Any) -> Any:
    """Безопасно сериализует dataclass в dict, иначе возвращает как есть."""
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _coordinator_snapshot(coord: SboomCoordinator) -> dict[str, Any]:
    # last_update_success сознательно НЕ выгружается: контракт
    # DataUpdateCoordinator обойдён (сбои полла глотаются, UpdateFailed не
    # бросается) — поле всегда True и дезинформировало bug-report'ы.
    # Честная доступность — coord.connected.
    return {
        "connected": coord.connected,
        "update_interval_sec": (
            coord.update_interval.total_seconds() if coord.update_interval else None
        ),
        "stopping": coord.stopping,
        "lyrics_cache_size": len(coord.lyrics.by_track),
        "lyrics_inflight_count": coord.lyrics.inflight_count,
        "client_host": coord.client.host,
        "client_port": coord.client.port,
    }


def _build_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coord: SboomCoordinator | None = getattr(entry, "runtime_data", None)

    payload: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "minor_version": getattr(entry, "minor_version", None),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "title": entry.title,
        },
        "coordinator": (
            async_redact_data(_coordinator_snapshot(coord), TO_REDACT) if coord else None
        ),
        "track": async_redact_data(_safe_dataclass(coord.track), TO_REDACT) if coord else None,
        "state": (
            async_redact_data(_safe_dataclass(coord.state), TO_REDACT) if coord else None
        ),
    }
    return payload


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Diagnostics для конкретной интеграции (Settings → Integrations → SBoom)."""
    return _build_diagnostics(hass, entry)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Diagnostics для конкретного устройства (Settings → Devices → SBoom)."""
    return _build_diagnostics(hass, entry)
