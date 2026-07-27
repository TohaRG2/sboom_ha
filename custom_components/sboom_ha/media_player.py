"""HA media_player entity для SberBoom-колонки."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .const import REPEAT_TO_CANONICAL
from .coordinator import SboomCoordinator
from .helpers import cover_url, provider_label

_LOGGER = logging.getLogger(__name__)

# Команды идут к колонке через единый WS с собственным lock — HA-параллелизм не нужен.
PARALLEL_UPDATES = 0

SUPPORTED = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    async_add_entities([SboomMediaPlayer(coordinator, entry)])


class SboomMediaPlayer(SboomEntity, MediaPlayerEntity):
    _attr_name = None  # = device name (через has_entity_name из SboomEntity)
    _attr_device_class = "speaker"  # строка == MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = SUPPORTED

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = self._device_unique_prefix

    # ─────────────────── state derivation ───────────────────

    @property
    def state(self) -> MediaPlayerState | None:
        track = self.coordinator.track
        if track is None:
            return MediaPlayerState.IDLE
        return MediaPlayerState.PLAYING if track.playing else MediaPlayerState.PAUSED

    @property
    def volume_level(self) -> float | None:
        st = self.coordinator.state
        if not st or st.volume_percent is None:
            return None
        return st.volume_percent / 100.0

    @property
    def is_volume_muted(self) -> bool | None:
        st = self.coordinator.state
        return st.muted if st else None

    @property
    def media_title(self) -> str | None:
        return self.coordinator.track.title if self.coordinator.track else None

    @property
    def media_artist(self) -> str | None:
        if not self.coordinator.track:
            return None
        return ", ".join(self.coordinator.track.artists) or None

    @property
    def media_album_name(self) -> str | None:
        return self.coordinator.track.album if self.coordinator.track else None

    @property
    def media_content_id(self) -> str | None:
        return self.coordinator.track.track_id if self.coordinator.track else None

    @property
    def media_channel(self) -> str | None:
        """Радиостанция (штатное поле HA для радио/ТВ-каналов)."""
        return self.coordinator.track.station_name if self.coordinator.track else None

    @property
    def media_content_type(self) -> str | None:
        # Без этого HA в more-info не выводит media_artist и фолбэкает на app_name.
        return MediaType.MUSIC if self.coordinator.track else None

    @property
    def media_duration(self) -> int | None:
        return self.coordinator.track.duration_sec if self.coordinator.track else None

    @property
    def media_position(self) -> int | None:
        return self.coordinator.track.position_sec if self.coordinator.track else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """Когда позиция была зафиксирована (UTC). HA сам инкрементит её со временем.

        Берём момент получения данных на стороне HA, а не timestamp часов
        колонки: рассинхрон часов ломал бы позицию во frontend.
        """
        track = self.coordinator.track
        if not track:
            return None
        if track.received_ts is not None:
            return datetime.fromtimestamp(track.received_ts, tz=UTC)
        if track.position_ts_ms is not None:  # fallback для старых данных
            return datetime.fromtimestamp(track.position_ts_ms / 1000, tz=UTC)
        return None

    @property
    def app_name(self) -> str | None:
        return provider_label(self.coordinator.track.provider) if self.coordinator.track else None

    @property
    def shuffle(self) -> bool | None:
        return self.coordinator.track.shuffle if self.coordinator.track else None

    @property
    def repeat(self) -> RepeatMode | None:
        if not self.coordinator.track or not self.coordinator.track.repeat:
            return None
        # Канон REPEAT_TO_CANONICAL совпадает со значениями RepeatMode.
        canon = REPEAT_TO_CANONICAL.get(self.coordinator.track.repeat.lower(), "off")
        return RepeatMode(canon)

    @property
    def media_image_url(self) -> str | None:
        track = self.coordinator.track
        if not track:
            return None
        # Каталог Zvuk → CDN по release_id; BT/радио → найденная по title+artist.
        return cover_url(track) or self.coordinator.current_cover()

    @property
    def media_image_remotely_accessible(self) -> bool:
        """Cover URLs are public CDN — let HA hand them to clients directly."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Контекст трека для дашбордов: плейлист/станция, тип, источник.

        Всё уже приходит в metadata — раньше просто не выставлялось.
        None-поля опускаем, чтобы не засорять атрибуты.
        """
        t = self.coordinator.track
        if t is None:
            return None
        attrs: dict[str, object] = {}
        if t.playlist_title:
            attrs["playlist"] = t.playlist_title
        if t.playlist_type:
            attrs["playlist_type"] = t.playlist_type  # endless (волна) / album / user
        if t.media_source:
            attrs["media_source"] = t.media_source     # MUSIC / RADIO / PODCAST
        if t.provider:
            attrs["provider"] = t.provider
        if t.playlist_liked is not None:
            attrs["playlist_liked"] = t.playlist_liked
        if t.child_mode is not None:
            attrs["child_mode"] = t.child_mode
        if t.buffering is not None:
            attrs["buffering"] = t.buffering
        return attrs or None

    # ─────────────────── commands ───────────────────
    #
    # Политика подтверждения: volume/mute не приходят push'ем, поэтому после
    # команды состояние патчится optimistic + запрашивается debounced refresh.
    # Play/pause/shuffle/repeat/seek колонка подтверждает push-событием почти
    # мгновенно — им достаточно optimistic-патча без refresh.

    async def async_set_volume_level(self, volume: float) -> None:
        target = max(0, min(100, int(volume * 100)))
        await self._run_command(
            self.coordinator.client.set_volume(target), action="set volume"
        )
        self.coordinator.apply_optimistic_state(volume_percent=target)
        await self.coordinator.async_request_refresh()

    async def async_volume_up(self) -> None:
        st = self.coordinator.state
        cur = st.volume_percent if st and st.volume_percent is not None else 50
        target = min(100, cur + 5)
        await self._run_command(
            self.coordinator.client.set_volume(target), action="volume up"
        )
        # Без optimistic-патча повторные нажатия в окне поллинга читали бы
        # старую громкость и не аккумулировались (3 × volume_up = +5, а не +15).
        self.coordinator.apply_optimistic_state(volume_percent=target)
        await self.coordinator.async_request_refresh()

    async def async_volume_down(self) -> None:
        st = self.coordinator.state
        cur = st.volume_percent if st and st.volume_percent is not None else 50
        target = max(0, cur - 5)
        await self._run_command(
            self.coordinator.client.set_volume(target), action="volume down"
        )
        self.coordinator.apply_optimistic_state(volume_percent=target)
        await self.coordinator.async_request_refresh()

    async def async_media_play(self) -> None:
        await self._run_command(self.coordinator.client.media_play(), action="play")
        self.coordinator.apply_optimistic_track(playing=True)

    async def async_media_pause(self) -> None:
        await self._run_command(self.coordinator.client.media_pause(), action="pause")
        self.coordinator.apply_optimistic_track(playing=False)

    async def async_media_next_track(self) -> None:
        await self._run_command(self.coordinator.client.media_next(), action="next track")

    async def async_media_previous_track(self) -> None:
        await self._run_command(self.coordinator.client.media_prev(), action="previous track")

    async def async_media_seek(self, position: float) -> None:
        await self._run_command(
            self.coordinator.client.seek_to(int(position)), action="seek"
        )

    async def async_mute_volume(self, mute: bool) -> None:
        cmd = self.coordinator.client.media_mute() if mute else self.coordinator.client.media_unmute()
        await self._run_command(cmd, action="mute" if mute else "unmute")
        self.coordinator.apply_optimistic_state(muted=mute)
        await self.coordinator.async_request_refresh()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._run_command(
            self.coordinator.client.media_shuffle(shuffle), action="set shuffle"
        )
        self.coordinator.apply_optimistic_track(shuffle=shuffle)

    async def async_set_repeat(self, repeat: str) -> None:
        await self._run_command(
            self.coordinator.client.media_repeat(repeat), action="set repeat"
        )
