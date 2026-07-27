"""Builders фейковых coordinator/entry для unit-тестов entity.

Использование:

    from tests._fakes import build_coordinator, make_track, make_state
    coord = build_coordinator(track=make_track(title="X"), state=make_state(volume=50))
    mp = SboomMediaPlayer(coord, coord.entry)

Не открывает WS, не подключается к сети — всё in-memory.
"""
from __future__ import annotations

# ВАЖНО: stubs должны быть установлены ДО любого `from sboom_ha.*` импорта.
from tests._ha_stubs import ConfigEntry, HomeAssistant, install_stubs

install_stubs()

# Теперь безопасно импортировать модули проекта.
from sboom_ha.api import SpeakerState, TrackInfo  # noqa: E402
from sboom_ha.const import (  # noqa: E402
    CONF_CLIENT_ID,
    CONF_CLIENT_NAME,
    CONF_DEVICE_FIRMWARE,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_PIN_ACCESS_TOKEN,
    CONF_PORT,
    DEFAULT_PORT,
)
from sboom_ha.coordinator import SboomCoordinator  # noqa: E402


def make_entry(
    *,
    host: str = "192.0.2.10",
    port: int = DEFAULT_PORT,
    device_id: str | None = "test-device-id",
    device_name: str = "Test Speaker",
    device_model: str = "test-model",
    device_firmware: str | None = "1.0.0",
    entry_id: str = "test_entry",
) -> ConfigEntry:
    """Фейковый ConfigEntry с типичным набором полей."""
    return ConfigEntry(
        data={
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_CLIENT_ID: "00000000-0000-0000-0000-000000000001",
            CONF_CLIENT_NAME: "Home Assistant",
            CONF_PIN_ACCESS_TOKEN: "test-pin-token-1234567890abcdef",
            CONF_DEVICE_ID: device_id,
            CONF_DEVICE_MODEL: device_model,
            CONF_DEVICE_NAME: device_name,
            CONF_DEVICE_FIRMWARE: device_firmware,
        },
        entry_id=entry_id,
    )


def make_track(
    *,
    title: str | None = "Test Track",
    artists: list[str] | None = None,
    album: str | None = "Test Album",
    track_id: str | None = "1001",
    release_id: str | None = "200",
    artist_ids: list[str] | None = None,
    provider: str | None = "zvuk",
    duration_sec: int | None = 240,
    position_sec: int | None = 60,
    position_ts_ms: int | None = None,
    playing: bool = True,
    shuffle: bool = False,
    repeat: str | None = "none",
    explicit: bool = False,
    liked: bool = False,
) -> TrackInfo:
    return TrackInfo(
        title=title,
        artists=list(artists) if artists is not None else ["Test Artist"],
        album=album,
        track_id=track_id,
        release_id=release_id,
        artist_ids=list(artist_ids) if artist_ids is not None else ["1"],
        provider=provider,
        duration_sec=duration_sec,
        position_sec=position_sec,
        position_ts_ms=position_ts_ms,
        playing=playing,
        shuffle=shuffle,
        repeat=repeat,
        explicit=explicit,
        liked=liked,
    )


def make_state(
    *,
    volume: int = 50,
    muted: bool = False,
) -> SpeakerState:
    return SpeakerState(volume_percent=volume, muted=muted)


def build_coordinator(
    *,
    entry: ConfigEntry | None = None,
    track: TrackInfo | None = None,
    state: SpeakerState | None = None,
    hass: HomeAssistant | None = None,
) -> SboomCoordinator:
    """Создать SboomCoordinator с заранее заполненными state/track. WS не открывается."""
    hass = hass or HomeAssistant()
    entry = entry or make_entry()
    coord = SboomCoordinator(hass, entry)
    coord.entry = entry  # already done by __init__, but explicit for clarity
    coord.track = track
    coord.state = state
    # Как в проде: координатор живёт в entry.runtime_data, entry известен HA.
    entry.runtime_data = coord
    hass.config_entries.add(entry)
    return coord
