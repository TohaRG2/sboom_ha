"""Общие утилиты для media_player, sensor, camera."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .const import COVER_SIZE, ZVUK_IMAGE_CDN

if TYPE_CHECKING:
    from .api import TrackInfo
    from .coordinator import SboomCoordinator


# Защита от мусорных значений timestamp (например при reboot колонки).
_MAX_EXTRAPOLATION_SEC = 600


def track_position(coordinator: SboomCoordinator) -> float | None:
    """Текущая позиция трека в секундах с экстраполяцией.

    База экстраполяции — received_monotonic (момент получения данных на
    стороне HA): часы колонки могут расходиться с часами HA, и завязка на
    position_ts_ms устройства сдвигала бы позицию (и караоке-лирику) на
    величину skew, а при часах колонки «в будущем» вовсе отключала
    экстраполяцию. Fallback на position_ts_ms — только для треков без
    отметки (старые записи в тестах/кэше).
    """
    track = coordinator.track
    if not track or track.position_sec is None:
        return None
    pos = float(track.position_sec)
    if track.playing:
        delta: float | None = None
        if track.received_monotonic is not None:
            delta = time.monotonic() - track.received_monotonic
        elif track.position_ts_ms:
            delta = (
                datetime.now(UTC).timestamp() * 1000 - track.position_ts_ms
            ) / 1000.0
        if delta is not None and 0 <= delta < _MAX_EXTRAPOLATION_SEC:
            speed = track.playback_speed or 1.0
            if speed <= 0:
                speed = 1.0
            pos += delta * speed
    if track.duration_sec:
        pos = min(pos, float(track.duration_sec))
    return pos


def lyrics_position(coordinator: SboomCoordinator) -> float | None:
    """Позиция для синхронизации лирики: track_position + пользовательский offset.

    Offset (options flow) компенсирует систематическое опережение/отставание
    текстов конкретной колонки. К media_position НЕ применяется.
    """
    pos = track_position(coordinator)
    if pos is None:
        return None
    offset = getattr(coordinator, "lyrics_offset", 0.0) or 0.0
    return max(0.0, pos + offset)


_PROVIDER_LABELS = {
    "zvuk": "Sber Звук",
    "salute": "Салют",
    "youtube": "YouTube",
    "spotify": "Spotify",
}


def provider_label(provider: str | None) -> str | None:
    """Человекочитаемое имя провайдера (zvuk → «Sber Звук»)."""
    if not provider:
        return None
    return _PROVIDER_LABELS.get(provider, provider)


def source_label(track: TrackInfo | None) -> str | None:
    """Плашка источника для караоке-кадра: «Плейлист · Провайдер»."""
    if track is None:
        return None
    parts = [p for p in (track.playlist_title, provider_label(track.provider)) if p]
    return " · ".join(parts) or None


def cover_url(track: TrackInfo) -> str | None:
    """URL обложки трека из public Zvuk CDN (без auth)."""
    if not track or track.provider != "zvuk":
        return None
    if track.release_id:
        return f"{ZVUK_IMAGE_CDN}?type=release&id={track.release_id}&size={COVER_SIZE}"
    if track.artist_ids:
        return f"{ZVUK_IMAGE_CDN}?type=artist&id={track.artist_ids[0]}&size={COVER_SIZE}"
    return None
