"""HA-зависимые общие хелперы: singleton ZvukClient и обход координаторов.

Выделены из services.py / websocket_api.py (аудит #19): раньше ленивый
singleton и поиск координаторов были скопированы между модулями, и
единственность инстанса держалась на совпадении строк-ключей.
`helpers.py` для этого не подходит — он намеренно чистый Python (без HA).
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .const import DOMAIN, ZVUK_CLIENT_KEY
from .coordinator import SboomCoordinator
from .zvuk_client import ZvukClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


def get_zvuk_client(hass: HomeAssistant) -> ZvukClient:
    """Единственный кешированный ZvukClient (ленивая инициализация).

    Отдельный HTTP-клиент со своим cookie jar (anti-bot Звука: 307-редирект +
    cookie ``spid``), поэтому не переиспользуем shared aiohttp-сессию HA.
    Закрывается по EVENT_HOMEASSISTANT_STOP (см. __init__).
    """
    client: ZvukClient | None = hass.data.get(ZVUK_CLIENT_KEY)
    if client is None:
        client = ZvukClient()
        hass.data[ZVUK_CLIENT_KEY] = client
    return client


def iter_coordinators(
    hass: HomeAssistant,
) -> Iterator[tuple[ConfigEntry, SboomCoordinator]]:
    """(entry, coordinator) для всех загруженных колонок SberBoom."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, SboomCoordinator):
            yield entry, coordinator
