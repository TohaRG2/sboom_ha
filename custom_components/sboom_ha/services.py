"""Регистрация custom services интеграции.

Сервисы регистрируются в async_setup (IQS action-setup): существуют всегда,
даже когда entry не загружен — невалидные вызовы падают с
ServiceValidationError, а не с «service not found».
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from ._deeplink import play_deeplink
from .const import BT_CMD_CONNECT, BT_CMD_DISCONNECT, BT_CMD_REMOVE, DOMAIN
from .coordinator import SboomCoordinator
from .zvuk_client import ZvukClient

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH_METADATA = "refresh_metadata"
SERVICE_REAUTH = "reauth"
SERVICE_BT_DEVICE = "bluetooth_device"
SERVICE_PLAY_MUSIC = "play_music"

_ZVUK_CLIENT_KEY = f"{DOMAIN}_zvuk_client"

_BT_CMD_MAP = {
    "connect": BT_CMD_CONNECT,
    "disconnect": BT_CMD_DISCONNECT,
    "remove": BT_CMD_REMOVE,
}

# device_id добавляет HA-таргетинг (`target:` в services.yaml) — сервис может
# прийти со строкой или списком. Схемы отклоняют мусор до входа в handler.
_TARGET_SCHEMA = {
    vol.Optional("device_id"): vol.Any(str, [str]),
}
SCHEMA_REFRESH_METADATA = vol.Schema(_TARGET_SCHEMA)
SCHEMA_REAUTH = vol.Schema(_TARGET_SCHEMA)
SCHEMA_BT_DEVICE = vol.Schema(
    {
        **_TARGET_SCHEMA,
        vol.Required("mac_address"): str,
        vol.Required("command"): vol.In(sorted(_BT_CMD_MAP)),
    }
)
# play_music: один из источников — url (zvuk.com/... или готовый staros://),
# id (+ kind), или query (поиск по названию в Звуке).
SCHEMA_PLAY_MUSIC = vol.Schema(
    {
        **_TARGET_SCHEMA,
        vol.Optional("url"): str,
        vol.Optional("query"): str,
        vol.Optional("id"): str,
        vol.Optional("kind"): vol.In(
            ["track", "artist", "release", "playlist", "podcast", "abook"]
        ),
    }
)


def _loaded_coordinators(hass: HomeAssistant) -> dict[str, SboomCoordinator]:
    """entry_id → coordinator для всех загруженных entries интеграции."""
    result: dict[str, SboomCoordinator] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, SboomCoordinator):
            result[entry.entry_id] = coordinator
    return result


def _coords_from_call(hass: HomeAssistant, call: ServiceCall) -> list[SboomCoordinator]:
    """Извлечь все SboomCoordinator'ы, на которые таргетится service-call.

    Поддерживает device_id-таргетинг. Если таргет не указан — все загруженные.
    Пустой результат — ошибка пользователя (entry не загружен или неверный
    device_id), о которой надо сказать явно, а не молча сделать ничего.
    """
    device_ids: list[str] | str = call.data.get("device_id", []) or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]

    all_coords = _loaded_coordinators(hass)

    if not device_ids:
        selected = list(all_coords.values())
    else:
        device_reg = dr.async_get(hass)
        selected = []
        for did in device_ids:
            device = device_reg.async_get(did)
            if not device:
                continue
            for entry_id in device.config_entries:
                if entry_id in all_coords:
                    selected.append(all_coords[entry_id])

    if not selected:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_loaded_speakers",
        )
    return selected


async def _handle_refresh_metadata(hass: HomeAssistant, call: ServiceCall) -> None:
    for coord in _coords_from_call(hass, call):
        await coord.async_request_refresh()


async def _handle_reauth(hass: HomeAssistant, call: ServiceCall) -> None:
    for coord in _coords_from_call(hass, call):
        coord.entry.async_start_reauth(hass)


async def _handle_bt_device(hass: HomeAssistant, call: ServiceCall) -> None:
    mac = call.data["mac_address"]
    cmd = _BT_CMD_MAP[call.data["command"]]
    for coord in _coords_from_call(hass, call):
        await coord.client.bt_device_command(mac, cmd)


def _zvuk_client(hass: HomeAssistant) -> ZvukClient:
    """Единственный кешированный ZvukClient (общий с websocket_api)."""
    client: ZvukClient | None = hass.data.get(_ZVUK_CLIENT_KEY)
    if client is None:
        client = ZvukClient()
        hass.data[_ZVUK_CLIENT_KEY] = client
    return client


async def _resolve_deeplink(hass: HomeAssistant, data: dict) -> str:
    """url / id+kind / query → staros://music-deeplink."""
    url = data.get("url")
    if url:
        if url.startswith("staros://"):
            return url  # готовый deeplink
        parsed = ZvukClient.parse_zvuk_url(url, None)
        if not parsed:
            raise ServiceValidationError(f"не удалось разобрать Звук-URL: {url}")
        return ZvukClient.build_deeplink(*parsed)

    if data.get("id"):
        parsed = ZvukClient.parse_zvuk_url(data["id"], data.get("kind"))
        if not parsed:
            raise ServiceValidationError(
                "для id укажите kind (track/artist/release/playlist/podcast)"
            )
        return ZvukClient.build_deeplink(*parsed)

    query = data.get("query")
    if query:
        deeplink = await _zvuk_client(hass).search_first_deeplink(query)
        if deeplink:
            return deeplink
        raise ServiceValidationError(
            f"Звук: по запросу «{query}» ничего не найдено (или поиск недоступен)"
        )

    raise ServiceValidationError(
        "play_music: укажите один из источников — url, id или query"
    )


async def _handle_play_music(hass: HomeAssistant, call: ServiceCall) -> None:
    deeplink = await _resolve_deeplink(hass, call.data)
    for coord in _coords_from_call(hass, call):
        if not await play_deeplink(coord.client, deeplink):
            raise ServiceValidationError(
                f"колонка отклонила deeplink: {deeplink}"
            )


def async_register_services(hass: HomeAssistant) -> None:
    """Регистрация служб (однократно, из async_setup)."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA):
        return

    async def _refresh_handler(call: ServiceCall) -> None:
        await _handle_refresh_metadata(hass, call)

    async def _reauth_handler(call: ServiceCall) -> None:
        await _handle_reauth(hass, call)

    async def _bt_device_handler(call: ServiceCall) -> None:
        await _handle_bt_device(hass, call)

    async def _play_music_handler(call: ServiceCall) -> None:
        await _handle_play_music(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_METADATA, _refresh_handler, schema=SCHEMA_REFRESH_METADATA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REAUTH, _reauth_handler, schema=SCHEMA_REAUTH
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BT_DEVICE, _bt_device_handler, schema=SCHEMA_BT_DEVICE
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_MUSIC, _play_music_handler, schema=SCHEMA_PLAY_MUSIC
    )
