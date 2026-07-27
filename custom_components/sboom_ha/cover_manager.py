"""Менеджер обложек для НЕкаталожного контента (Bluetooth/радио).

Каталог Zvuk отдаёт обложку синхронно по release_id (helpers.cover_url). У
BT/радио каталожного id нет — обложку ищем асинхронно по title+artist
(cover_client: iTunes → Deezer) и держим ОДИН волатильный слот на текущий
трек. НЕ персистится: контент эфемерный. В отличие от лирики, радио здесь
НЕ исключается — обложке синхронизация не нужна, только название песни.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .const import DOMAIN
from .cover_client import fetch_cover

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .api import TrackInfo

_LOGGER = logging.getLogger(__name__)


def _cover_key(track: TrackInfo) -> str | None:
    """Ключ обложки — только для НЕкаталожных треков (BT/радио).

    Каталог (track_id) уже имеет обложку через CDN → None (менеджер их не трогает).
    """
    if track.track_id:
        return None
    if track.title and track.artists:
        return f"{track.title}|{','.join(track.artists)}".lower()
    return None


class CoverManager:
    """Волатильный поиск обложки по title+artist (один слот, без персиста)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        http_session: aiohttp.ClientSession,
        *,
        on_update: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._http = http_session
        self._on_update = on_update
        self._key: str | None = None
        self._url: str | None = None
        self._inflight: set[str] = set()

    def current_for(self, track: TrackInfo | None) -> str | None:
        """URL найденной обложки для текущего некаталожного трека, либо None."""
        if not track:
            return None
        key = _cover_key(track)
        if key is not None and key == self._key:
            return self._url
        return None

    def maybe_fetch(self, track: TrackInfo | None) -> None:
        """Запустить поиск обложки, если это некаталожный трек и ещё не искали."""
        if not track:
            return
        key = _cover_key(track)
        if key is None or key == self._key or key in self._inflight:
            return
        self._inflight.add(key)
        self._entry.async_create_background_task(
            self._hass,
            self._fetch(key, track.title, ", ".join(track.artists)),
            name=f"{DOMAIN}-cover",
        )

    async def _fetch(self, key: str, title: str, artist: str) -> None:
        try:
            url = await fetch_cover(self._http, title, artist)
            if url is None:
                return  # не нашли/ошибка — retry при следующем track-update
            self._key = key
            self._url = url
            self._on_update()
        finally:
            self._inflight.discard(key)
