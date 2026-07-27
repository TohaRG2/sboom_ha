"""Менеджер lyrics: кэш по трекам, фоновые загрузки, персист в HA Store.

Выделен из SboomCoordinator (SRP): координатор отвечает за WS-сессию и
state, менеджер — за жизненный цикл текстов песен. Координатор дергает
`maybe_fetch`/`current_for`, менеджер сообщает о готовности через
callback `on_update` (перерисовка entities).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LYRICS_CACHE_MAX
from .lyrics_client import Lyrics, fetch_lyrics, lyrics_from_dict, lyrics_to_dict

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.config_entries import ConfigEntry

    from .api import TrackInfo

_LOGGER = logging.getLogger(__name__)

# Lyrics-кеш персистится в HA Store с debounce — чтобы не писать на диск
# на каждый найденный трек.
LYRICS_STORE_VERSION = 1
LYRICS_SAVE_DELAY_SEC = 30


def _synthetic_key(track: TrackInfo) -> str | None:
    """Ключ для НЕкаталожного трека без track_id (напр. Bluetooth): title|artists.

    Радио исключено сознательно: колонка отдаёт позицию ЭФИРА (стрима), а не
    песни (напр. 1224 c при песне 36–250 c) — синхронизировать лирику нечем,
    караоке для радио смысла не имеет. None → fetch и current_for его пропустят.
    """
    if track.media_source == "RADIO":
        return None
    if track.title and track.artists:
        return f"{track.title}|{','.join(track.artists)}".lower()
    return None


class LyricsManager:
    """Кэш track_id → Lyrics (None = искали, не нашли) + загрузка и персист."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        http_session: aiohttp.ClientSession,
        *,
        enabled: bool,
        netease_fallback: bool = True,
        on_update: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._http = http_session
        self._enabled = enabled
        self._netease_fallback = netease_fallback
        self._on_update = on_update
        self.by_track: dict[str, Lyrics | None] = {}
        self._inflight: set[str] = set()
        # Волатильная лирика для НЕкаталожного контента (BT/радио — нет track_id).
        # НЕ персистится в Store: BT — почти всегда одноразовая чужая музыка,
        # радио — эфемерно, засорять диск-кэш незачем. Один слот на текущий
        # трек: пока играет тот же — не перезапрашиваем API каждый poll.
        self._volatile_key: str | None = None
        self._volatile: Lyrics | None = None
        # Персистентный кеш (JSON в .storage/, переживает рестарты HA).
        self._store: Store = Store(
            hass, LYRICS_STORE_VERSION, f"{DOMAIN}_lyrics_{entry.entry_id}"
        )

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def current_for(self, track: TrackInfo | None) -> Lyrics | None:
        """Lyrics для трека (или None если ещё не загружено / не нашлось)."""
        if not track:
            return None
        if track.track_id:  # каталог Zvuk — персистентный кэш
            return self.by_track.get(track.track_id)
        # BT/радио — волатильный слот (без обращения к диск-кэшу).
        key = _synthetic_key(track)
        if key is not None and key == self._volatile_key:
            return self._volatile
        return None

    def maybe_fetch(self, track: TrackInfo | None) -> None:
        """Запустить background fetch для трека, если ещё не загружали."""
        if not self._enabled or not track or not track.title or not track.artists:
            return
        if track.track_id:
            self._schedule_catalog_fetch(track)
        else:
            self._schedule_volatile_fetch(track)

    def _schedule_catalog_fetch(self, track: TrackInfo) -> None:
        """Каталожный трек (track_id): персистентный кэш + Store."""
        tid = track.track_id
        if tid in self.by_track or tid in self._inflight:
            return
        # Простая защита от роста кэша: при превышении дропаем самый старый.
        if len(self.by_track) >= LYRICS_CACHE_MAX:
            self.by_track.pop(next(iter(self.by_track)), None)
        self._inflight.add(tid)
        # Задача привязана к entry: при unload/reload HA сам её отменит —
        # иначе fetch жил бы дольше владельца и писал в мёртвый Store.
        self._entry.async_create_background_task(
            self._hass,
            self._fetch(
                tid,
                track.title,
                ", ".join(track.artists),
                track.album,
                track.duration_sec,
            ),
            name=f"{DOMAIN}-lyrics-{tid}",
        )

    def _schedule_volatile_fetch(self, track: TrackInfo) -> None:
        """НЕкаталожный трек (BT/радио): fetch напрямую, без диск-кэша."""
        key = _synthetic_key(track)
        if key is None or key == self._volatile_key or key in self._inflight:
            return
        self._inflight.add(key)
        self._entry.async_create_background_task(
            self._hass,
            self._fetch_volatile(
                key,
                track.title,
                ", ".join(track.artists),
                track.album,
                track.duration_sec,
            ),
            name=f"{DOMAIN}-lyrics-volatile",
        )

    async def _fetch_volatile(
        self,
        key: str,
        title: str,
        artist: str,
        album: str | None,
        duration_sec: int | None,
    ) -> None:
        """Загрузка для BT/радио: результат в волатильный слот, НЕ в Store."""
        try:
            result = await fetch_lyrics(
                self._http, title, artist, album, duration_sec,
                use_netease=self._netease_fallback,
            )
            if result is None:
                return  # сетевая ошибка — retry при следующем track-update
            self._volatile_key = key
            self._volatile = result
            self._on_update()
        finally:
            self._inflight.discard(key)

    async def _fetch(
        self,
        track_id: str,
        title: str,
        artist: str,
        album: str | None,
        duration_sec: int | None,
    ) -> None:
        try:
            result = await fetch_lyrics(
                self._http, title, artist, album, duration_sec,
                use_netease=self._netease_fallback,
            )
            if result is None:
                # Сетевая ошибка — НЕ кэшируем, дадим retry при следующем track-update.
                _LOGGER.debug("lyrics fetch error for %s — will retry later", track_id)
                return
            self.by_track[track_id] = result
            # Персист в Store с debounce — не пишем на диск на каждый трек.
            self._store.async_delay_save(self.cache_snapshot, LYRICS_SAVE_DELAY_SEC)
            _LOGGER.debug(
                "lyrics for %s (%r — %r): %s",
                track_id, title, artist,
                "found" if result.plain or result.synced
                else ("instrumental" if result.instrumental else "not_found"),
            )
            self._on_update()
        finally:
            self._inflight.discard(track_id)

    def cache_snapshot(self) -> dict[str, dict[str, Any]]:
        """Снимок кеша для персиста (только реальные Lyrics, без None)."""
        return {
            tid: lyrics_to_dict(lyr)
            for tid, lyr in self.by_track.items()
            if lyr is not None
        }

    async def async_load(self) -> None:
        """Загрузить персистентный кеш из HA Store при старте."""
        try:
            stored = await self._store.async_load()
        except Exception:  # повреждённый файл — не критично, стартуем с пустым
            _LOGGER.warning("lyrics cache load failed", exc_info=True)
            return
        if not isinstance(stored, dict):
            return
        for tid, payload in list(stored.items())[:LYRICS_CACHE_MAX]:
            if isinstance(payload, dict):
                try:
                    self.by_track[tid] = lyrics_from_dict(payload)
                except Exception:  # битая запись — пропускаем
                    continue
        _LOGGER.debug("lyrics cache loaded: %d entries", len(self.by_track))

    async def async_flush(self) -> None:
        """Немедленный save (при unload — иначе debounced save писал бы
        в Store уже мёртвого entry)."""
        if not self.by_track:
            return
        try:
            await self._store.async_save(self.cache_snapshot())
        except Exception:  # не мешаем выгрузке из-за диска
            _LOGGER.debug("lyrics cache flush failed", exc_info=True)
