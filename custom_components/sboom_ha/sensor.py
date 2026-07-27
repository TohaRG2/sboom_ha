"""Сенсоры sboom_ha: lyrics + декларативные read-only сенсоры подсистем GET_STATE."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from ._entity_base import SboomEntity
from ._models import DeviceState
from ._schedule import next_alarm, next_timer
from .coordinator import SboomCoordinator
from .helpers import lyrics_position
from .lyrics_client import current_line
from .lyrics_manager import _synthetic_key

# Read-only сенсоры, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SboomSensorSpec:
    """Декларативное описание тривиального read-only сенсора.

    Все значения читаются из coordinator через value_fn/attrs_fn — сенсор
    описывается одной записью в SENSOR_SPECS вместо отдельного класса
    (тот же паттерн, что BUTTONS в button.py и SWITCHES в switch.py).
    """

    key: str  # суффикс unique_id
    translation_key: str
    icon: str | None = None
    value_fn: Callable[[SboomCoordinator], Any]
    attrs_fn: Callable[[SboomCoordinator], dict[str, Any] | None] | None = None
    native_unit: str | None = None
    state_class: Any = None  # SensorStateClass; Any — чтобы не тянуть импорт в тестовые stub'ы
    device_class: Any = None  # SensorDeviceClass; аналогично
    entity_category: EntityCategory | None = None
    enabled_default: bool = True
    # Для «железных» сенсоров (libiio/Zigbee): создавать, только если модель
    # реально умеет. None = сенсор доступен всегда (обычные подсистемы GET_STATE).
    available_fn: Callable[[SboomCoordinator], bool] | None = None


def _dev(c: SboomCoordinator) -> DeviceState | None:
    """Подсистемы устройства из последнего GET_STATE, либо None."""
    return c.state.device if c.state else None


def _link_count(dev: DeviceState | None) -> int | None:
    """Сколько устройств связано: sbercast + группы селектора + саундбар."""
    if dev is None:
        return None
    n = len(dev.sbercast_devices) + sum(len(v) for v in dev.selector_groups.values())
    if dev.soundbar_group:
        n += 1
    return n


def _playback_mode(c: SboomCoordinator) -> str | None:
    """Тип текущего воспроизведения: music/wave/podcast/radio/bluetooth.

    Единого поля-режима у колонки нет: radio виден по media_source, bluetooth —
    по media_source/app, а wave/podcast — по playlistType. Собираем из трека
    (который для радио/BT добирается из GET_STATE), None когда ничего не играет.
    """
    t = c.track
    if t is None:
        return None
    if t.media_source == "BLUETOOTH":
        return "bluetooth"
    if t.media_source == "RADIO":
        return "radio"
    pt = (t.playlist_type or "").lower()
    if pt == "podcast":
        return "podcast"
    if pt == "endless":
        return "wave"
    return "music"


def _link_attrs(dev: DeviceState | None) -> dict[str, Any] | None:
    """Разбивка связки по источникам; None когда ничего не связано."""
    if dev is None:
        return None
    attrs: dict[str, Any] = {}
    if dev.sbercast_devices:
        attrs["sbercast_devices"] = dev.sbercast_devices
    if dev.selector_groups:
        attrs["selector_groups"] = dev.selector_groups
    if dev.soundbar_group:
        attrs["soundbar"] = dev.soundbar_group
    return attrs or None


SENSOR_SPECS: tuple[SboomSensorSpec, ...] = (
    # Яркость LED-дисплея колонки (0-100%).
    SboomSensorSpec(
        key="led_brightness",
        translation_key="led_brightness",
        icon="mdi:brightness-6",
        native_unit=PERCENTAGE,
        state_class="measurement",  # строка == SensorStateClass.MEASUREMENT
        value_fn=lambda c: dev.led_brightness if (dev := _dev(c)) else None,
    ),
    # Количество установленных будильников. Список — в атрибутах.
    SboomSensorSpec(
        key="alarms",
        translation_key="alarms",
        icon="mdi:alarm",
        value_fn=lambda c: dev.alarms_count if (dev := _dev(c)) else None,
        attrs_fn=lambda c: {"alarms": dev.alarms} if (dev := _dev(c)) else None,
    ),
    # Количество активных таймеров. Список — в атрибутах.
    SboomSensorSpec(
        key="timers",
        translation_key="timers",
        icon="mdi:timer-outline",
        value_fn=lambda c: dev.timers_count if (dev := _dev(c)) else None,
        attrs_fn=lambda c: {"timers": dev.timers} if (dev := _dev(c)) else None,
    ),
    # Время ближайшего срабатывания будильника (timestamp).
    SboomSensorSpec(
        key="next_alarm",
        translation_key="next_alarm",
        icon="mdi:alarm-check",
        device_class="timestamp",
        value_fn=lambda c: (
            next_alarm(dev.alarms, datetime.now(UTC)) if (dev := _dev(c)) else None
        ),
    ),
    # Время окончания ближайшего таймера (timestamp).
    SboomSensorSpec(
        key="next_timer",
        translation_key="next_timer",
        icon="mdi:timer-check-outline",
        device_class="timestamp",
        value_fn=lambda c: (
            next_timer(dev.timers, datetime.now(UTC)) if (dev := _dev(c)) else None
        ),
    ),
    # Активное (играющее) приложение; весь z-order стек — в атрибутах.
    SboomSensorSpec(
        key="active_app",
        translation_key="active_app",
        icon="mdi:application",
        value_fn=lambda c: dev.active_app if (dev := _dev(c)) else None,
        attrs_fn=lambda c: (
            {"app_stack": dev.app_stack} if (dev := _dev(c)) and dev.app_stack else None
        ),
    ),
    # Приложение на переднем плане (current_app) — в отличие от active_app
    # (играющее). Пусто, когда ничего не открыто.
    SboomSensorSpec(
        key="foreground_app",
        translation_key="foreground_app",
        icon="mdi:cellphone-screenshot",
        value_fn=lambda c: dev.foreground_app if (dev := _dev(c)) else None,
    ),
    # Тип воспроизведения: music/wave/podcast/radio/bluetooth. В отличие от
    # active_app (=music для всего аудио), различает радио, BT и подкаст —
    # работает даже когда media_player пуст (радио/BT без trackId).
    SboomSensorSpec(
        key="playback_mode",
        translation_key="playback_mode",
        icon="mdi:playlist-play",
        value_fn=_playback_mode,
    ),
    # Персона голосового ассистента (afina/joy/sber).
    SboomSensorSpec(
        key="assistant_character",
        translation_key="assistant_character",
        icon="mdi:account-voice",
        value_fn=lambda c: dev.assistant_character if (dev := _dev(c)) else None,
    ),
    # Режим multiroom (NONE/…). При объединении в стереопару — канал (L/R) и
    # устройство-партнёр в атрибутах.
    SboomSensorSpec(
        key="multiroom_mode",
        translation_key="multiroom_mode",
        icon="mdi:speaker-multiple",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: dev.multiroom_mode if (dev := _dev(c)) else None,
        attrs_fn=lambda c: (
            {
                "stereo_pair_active": dev.stereo_pair_active,
                "stereo_pair_channel": dev.stereo_pair_channel,
                "stereo_pair_device": dev.stereo_pair_device,
            }
            if (dev := _dev(c))
            and (dev.stereo_pair_active or dev.stereo_pair_channel or dev.stereo_pair_device)
            else None
        ),
    ),
    # Межустройственная связка: cast-группы, саундбар, связанные устройства
    # Sber (SberBox/ТВ/колонки). На одиночной колонке = 0; при объединении
    # (farfield/multiroom/cast) показывает число связанных устройств, а разбивку
    # (sbercast/группы селектора/саундбар) — в атрибутах. Diagnostic, выключен
    # по умолчанию (обычно пусто).
    SboomSensorSpec(
        key="device_links",
        translation_key="device_links",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: _link_count(_dev(c)),
        attrs_fn=lambda c: _link_attrs(_dev(c)),
    ),
    # Тип сетевого подключения колонки (WIFI/…).
    SboomSensorSpec(
        key="network_type",
        translation_key="network_type",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: dev.network_type if (dev := _dev(c)) else None,
    ),
    # Спаренные Bluetooth-устройства. State = количество, список — в атрибутах.
    SboomSensorSpec(
        key="paired_bt",
        translation_key="paired_bt",
        icon="mdi:bluetooth-connect",
        value_fn=lambda c: len(c.paired_bt),
        attrs_fn=lambda c: {
            "devices": [
                {"mac": d.mac, "name": d.name, "connected": d.connected}
                for d in c.paired_bt
            ]
        },
    ),
    # IP-адрес колонки в локальной сети (network.ip). Diagnostic.
    SboomSensorSpec(
        key="ip_address",
        translation_key="ip_address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.network_ip if (dev := _dev(c)) else None,
    ),
    # Канал прошивки (device_segments, напр. "OpenBeta"). Diagnostic.
    SboomSensorSpec(
        key="firmware_channel",
        translation_key="firmware_channel",
        icon="mdi:test-tube",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.firmware_channel if (dev := _dev(c)) else None,
    ),
    # Часовой пояс колонки (time.timezone_id) + смещение в атрибутах. Diagnostic.
    SboomSensorSpec(
        key="timezone",
        translation_key="timezone",
        icon="mdi:map-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.timezone_id if (dev := _dev(c)) else None,
        attrs_fn=lambda c: (
            {"offset_hours": dev.timezone_offset_sec / 3600}
            if (dev := _dev(c)) and dev.timezone_offset_sec is not None
            else None
        ),
    ),
    # Возрастной режим профиля (user_settings). Профильные флаги — в атрибутах.
    SboomSensorSpec(
        key="age_mode",
        translation_key="age_mode",
        icon="mdi:account-child",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.age_mode if (dev := _dev(c)) else None,
        attrs_fn=lambda c: (
            {
                "multi_profile": dev.multi_profile,
                "child_voice_explicit": dev.child_voice_explicit,
            }
            if (dev := _dev(c)) and dev.multi_profile is not None
            else None
        ),
    ),
    # Рассинхрон часов колонки и HA (сек): device_unixtime − now. Помогает
    # диагностировать сдвиг караоке/позиции. Значение включает возраст poll'а
    # (до volume_poll_interval), поэтому это грубый индикатор минутного skew,
    # а не точный тайминг. Diagnostic, выключен по умолчанию.
    SboomSensorSpec(
        key="clock_skew",
        translation_key="clock_skew",
        icon="mdi:clock-alert-outline",
        native_unit="s",
        state_class="measurement",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: (
            round(dev.device_unixtime - time.time())
            if (dev := _dev(c)) and dev.device_unixtime is not None
            else None
        ),
    ),
    # Сырые Wi-Fi-координаты колонки. В отличие от device_tracker (state =
    # имя зоны, «Дома»/«Не дома»), здесь state = "lat, lon", а lat/lon/
    # accuracy/source — в атрибутах для карточек и шаблонов. Diagnostic,
    # выключен по умолчанию (координаты — чувствительные данные).
    SboomSensorSpec(
        key="coordinates",
        translation_key="coordinates",
        icon="mdi:map-marker",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: (
            f"{dev.latitude}, {dev.longitude}"
            if (dev := _dev(c)) and dev.latitude is not None and dev.longitude is not None
            else None
        ),
        attrs_fn=lambda c: (
            {
                "latitude": dev.latitude,
                "longitude": dev.longitude,
                "gps_accuracy": dev.location_accuracy,
                "source": dev.location_source,
            }
            if (dev := _dev(c)) and dev.latitude is not None
            else None
        ),
    ),
)


# «Железные» сенсоры — создаются только на моделях, где probe нашёл датчик
# (см. coordinator._probe_hw_capabilities). У большинства колонок Sber их нет.
HW_SENSOR_SPECS: tuple[SboomSensorSpec, ...] = (
    # Освещённость в комнате (датчик HX3203 через libiio), люксы.
    SboomSensorSpec(
        key="illuminance",
        translation_key="illuminance",
        native_unit="lx",
        state_class="measurement",
        device_class="illuminance",
        available_fn=lambda c: c.iio_cap.has_illuminance,
        value_fn=lambda c: c.iio_reading.illuminance_lux,
    ),
    # Температура SoC колонки (hwmon через libiio), °C. Diagnostic.
    SboomSensorSpec(
        key="soc_temperature",
        translation_key="soc_temperature",
        native_unit="°C",
        state_class="measurement",
        device_class="temperature",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda c: c.iio_cap.has_thermal,
        value_fn=lambda c: c.iio_reading.soc_temp_c,
    ),
    # Инвентарь Zigbee-устройств колонки (debug-CLI). State = количество,
    # список (модель/производитель/RSSI/питание) — в атрибутах. Diagnostic.
    # Только чтение инвентаря: состояние/управление устройствами CLI не даёт.
    SboomSensorSpec(
        key="zigbee_inventory",
        translation_key="zigbee_inventory",
        icon="mdi:zigbee",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda c: c.has_zigbee_cli,
        value_fn=lambda c: len(c.zigbee_devices),
        attrs_fn=lambda c: {
            "devices": [
                {
                    "ieee": d.ieee, "model": d.model, "manufacturer": d.manufacturer,
                    "power_source": d.power_source, "rssi": d.rssi, "state": d.state,
                }
                for d in c.zigbee_devices
            ]
        },
    ),
    # Инвентарь Matter-устройств колонки (debug-CLI). State = количество,
    # список (node_id/модель/серийник/RSSI) — в атрибутах. Формат таблицы
    # подтверждён на реальном устройстве. Только чтение инвентаря. Diagnostic.
    SboomSensorSpec(
        key="matter_inventory",
        translation_key="matter_inventory",
        icon="mdi:home-automation",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda c: c.has_matter_cli,
        value_fn=lambda c: len(c.matter_devices),
        attrs_fn=lambda c: {
            "devices": [
                {"node_id": d.node_id, "model": d.model,
                 "serial": d.serial, "rssi": d.rssi}
                for d in c.matter_devices
            ]
        } if c.matter_devices else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SboomLyricsCurrentLineSensor(coordinator, entry),
        SboomLyricsFullSensor(coordinator, entry),
        *(SboomDeviceSensor(coordinator, entry, spec) for spec in SENSOR_SPECS),
    ]
    # «Железные» сенсоры — только если модель реально их умеет (определено
    # при старте через probe). У большинства колонок Sber их НЕТ.
    for spec in HW_SENSOR_SPECS:
        if spec.available_fn(coordinator):
            entities.append(SboomDeviceSensor(coordinator, entry, spec))
    async_add_entities(entities)


class SboomDeviceSensor(SboomEntity, SensorEntity):
    """Generic-сенсор, полностью описанный через SboomSensorSpec."""

    def __init__(
        self,
        coordinator: SboomCoordinator,
        entry: ConfigEntry,
        spec: SboomSensorSpec,
    ) -> None:
        super().__init__(coordinator, entry)
        self._spec = spec
        self._attr_unique_id = f"{self._device_unique_prefix}_{spec.key}"
        self._attr_translation_key = spec.translation_key
        self._attr_icon = spec.icon
        self._attr_native_unit_of_measurement = spec.native_unit
        self._attr_state_class = spec.state_class
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        self._attr_entity_registry_enabled_default = spec.enabled_default

    @property
    def native_value(self) -> Any:
        return self._spec.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._spec.attrs_fn is None:
            return None
        return self._spec.attrs_fn(self.coordinator)


class SboomLyricsCurrentLineSensor(SboomEntity, SensorEntity):
    """State = текущая строка lyrics, активная по позиции трека.

    Для треков без synced-текста сенсор остаётся available со state=None
    (unknown): «нет лирики» — валидное состояние данных, а не недоступность
    устройства. Иначе автоматизации ловили бы ложные unavailable на каждом
    треке без текста.
    """

    _attr_translation_key = "lyrics_current_line"
    _attr_icon = "mdi:script-text"

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics_current_line"
        self._last_line: str | None = None
        self._unsub_tick = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule_tick(0.5)
        self.async_on_remove(self._cancel_tick)

    @callback
    def _cancel_tick(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None

    @callback
    def _schedule_tick(self, delay: float) -> None:
        self._cancel_tick()
        self._unsub_tick = async_call_later(self.hass, delay, self._tick)

    @callback
    def _tick(self, _now: datetime) -> None:
        line = self._compute_line()
        if line != self._last_line:
            self._last_line = line
            self.async_write_ha_state()
        # Самопланирование на границу следующей строки (а не жёсткий 1s-тик):
        # строка меняется без запаздывания до секунды.
        self._schedule_tick(self._next_tick_delay())

    def _next_tick_delay(self) -> float:
        lyrics = self.coordinator.current_lyrics()
        track = self.coordinator.track
        pos = lyrics_position(self.coordinator)
        if (
            lyrics and lyrics.timeline
            and track and track.playing
            and pos is not None
        ):
            next_ts = next((ts for ts, _ in lyrics.timeline if ts > pos), None)
            if next_ts is not None:
                return min(2.0, max(0.25, next_ts - pos))
        return 1.0

    def _compute_line(self) -> str | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics or not lyrics.timeline:
            return None
        pos = lyrics_position(self.coordinator)
        if pos is None:
            return None
        line = current_line(lyrics.timeline, pos)
        return line[:255] if line else None

    @property
    def native_value(self) -> str | None:
        return self._compute_line()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics:
            return None
        return {
            "source": lyrics.source,
            "instrumental": lyrics.instrumental,
            "synced": lyrics.timeline is not None,
        }


class SboomLyricsFullSensor(SboomEntity, SensorEntity):
    """State = 'available' / 'instrumental' / 'not_found'. Полный текст — в атрибутах."""

    _attr_translation_key = "lyrics"
    _attr_icon = "mdi:text"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # включается вручную если нужен
    # ENUM: набор состояний определяется нашим же кодом (native_value ниже) —
    # HA получает переводимые состояния и валидацию значений.
    _attr_device_class = "enum"  # строка == SensorDeviceClass.ENUM
    _attr_options: list[str] = ["no_track", "loading", "available", "instrumental", "not_found"]  # noqa: RUF012 — контракт HA: list

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics"

    @property
    def native_value(self) -> str | None:
        track = self.coordinator.track
        # Лирика возможна для каталога (track_id) и Bluetooth (synthetic-ключ);
        # радио исключено (позиция эфирная — синк невозможен → _synthetic_key None).
        if not track or (not track.track_id and _synthetic_key(track) is None):
            return "no_track"
        lyrics = self.coordinator.current_lyrics()
        if lyrics is None:
            # запрос ещё не завершён ИЛИ ошибка сети
            return "loading"
        if lyrics.instrumental:
            return "instrumental"
        if lyrics.plain or lyrics.synced:
            return "available"
        return "not_found"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics:
            return None
        return {
            "source": lyrics.source,
            "plain_lyrics": lyrics.plain,
            "synced_lyrics": lyrics.synced,
            "instrumental": lyrics.instrumental,
        }
