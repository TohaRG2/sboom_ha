"""Тесты клиентов аппаратных сенсоров (libiio) и Zigbee-CLI — на реальных
захватах с колонки (tests/fixtures/iio_context.xml, zigbee_list.txt)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sboom_ha.cli4242 import (
    Cli4242Client,
    matter_count,
    parse_matter_list,
    parse_zigbee_list,
)
from sboom_ha.iio_client import IioCapability, parse_context, parse_read_value

_FIX = Path(__file__).parent / "fixtures"


# ─────────────────── libiio ───────────────────

def test_parse_context_detects_real_sensors():
    """Реальный IIO-контекст R2: находит датчик света и термодатчик по каналам."""
    cap = parse_context((_FIX / "iio_context.xml").read_text())
    assert cap.has_illuminance and cap.illuminance_device == "iio:device0"
    assert cap.has_thermal and cap.thermal_device == "hwmon0"
    assert cap.any


def test_parse_context_empty_when_no_sensors():
    """Плата без нужных датчиков (или мусор) → пустая capability, не падаем."""
    assert parse_context("<context></context>").any is False
    assert parse_context("garbage").any is False
    assert parse_context("").any is False


def test_parse_read_value_libiio_format():
    """Формат ответа libiio '<len>\\n<value>\\0'."""
    assert parse_read_value(b"4\n168\x00") == "168"
    assert parse_read_value(b"6\n38600\x00") == "38600"
    assert parse_read_value(b"-2\nerr\x00") is None  # отрицательная длина = ошибка
    assert parse_read_value(b"garbage") is None


def test_iio_capability_flags():
    assert IioCapability().any is False
    assert IioCapability(illuminance_device="x").has_illuminance is True


# ─────────────────── zigbee CLI ───────────────────

def test_parse_zigbee_list_real_devices():
    """Реальная таблица `zigbee list` c 3 устройствами (реле + ИК + кнопка)."""
    devs = parse_zigbee_list((_FIX / "zigbee_list.txt").read_text())
    assert len(devs) == 3
    relay = devs[0]
    assert relay.model == "TS0002"
    assert relay.ieee == "A4C1384D8FBABD5D"
    assert relay.manufacturer == "_TZ3000_5gey1ohx"
    assert relay.power_source == "M1Phase"
    assert relay.rssi == -58
    assert relay.state == "Interview Done"
    # battery-устройства
    assert {d.power_source for d in devs[1:]} == {"Battery"}
    assert {d.model for d in devs} == {"TS0002", "TS1201", "TS004F"}


def test_parse_zigbee_list_empty():
    """Пустая сеть → пустой список (не None, не падение)."""
    assert parse_zigbee_list("List empty") == []
    assert parse_zigbee_list("") == []
    # только заголовок без данных
    header = "| # | IEEE Address | NWK | XID | State | Manufacturer name | Model ID | Pwr src | OTA perm. | App | Has OTA | RSSI |\n|---|---|---|---|---|---|---|---|---|---|---|---|"
    assert parse_zigbee_list(header) == []


# ─────────── условное создание HW-сенсоров по capability ───────────

def test_hw_sensors_hidden_without_capability():
    """Без обнаруженных датчиков (обычная колонка Sber) — HW-сенсоры НЕ
    создаются: available_fn возвращает False для всех."""
    from tests._fakes import build_coordinator, make_state
    from tests._ha_stubs import install_stubs
    install_stubs()
    from sboom_ha.sensor import HW_SENSOR_SPECS
    coord = build_coordinator(state=make_state())
    # по умолчанию iio_cap пуст, has_zigbee_cli False
    assert all(not s.available_fn(coord) for s in HW_SENSOR_SPECS)


def test_hw_sensors_appear_with_capability_and_read_values():
    """Модель с датчиками (R2): доступны все три HW-сенсора, значения читаются."""
    from tests._fakes import build_coordinator, make_state
    from tests._ha_stubs import install_stubs
    install_stubs()
    from sboom_ha.cli4242 import ZigbeeDevice
    from sboom_ha.iio_client import IioCapability, IioReading
    from sboom_ha.sensor import HW_SENSOR_SPECS

    coord = build_coordinator(state=make_state())
    coord.iio_cap = IioCapability(illuminance_device="iio:device0", thermal_device="hwmon0")
    coord.has_zigbee_cli = True
    coord.iio_reading = IioReading(illuminance_lux=168.0, soc_temp_c=38.6)
    coord.zigbee_devices = [
        ZigbeeDevice("A4C1384D8FBABD5D", "8E0F", "Interview Done",
                     "_TZ3000_5gey1ohx", "TS0002", "M1Phase", -58),
    ]
    by_key = {s.key: s for s in HW_SENSOR_SPECS}
    assert set(k for k, s in by_key.items() if s.available_fn(coord)) == {
        "illuminance", "soc_temperature", "zigbee_inventory"
    }
    assert by_key["illuminance"].value_fn(coord) == 168.0
    assert by_key["soc_temperature"].value_fn(coord) == 38.6
    assert by_key["zigbee_inventory"].value_fn(coord) == 1
    attrs = by_key["zigbee_inventory"].attrs_fn(coord)
    assert attrs["devices"][0]["model"] == "TS0002"


def test_hw_partial_capability_only_available_sensors():
    """Только термодатчик (без ALS/Zigbee) → создаётся лишь soc_temperature."""
    from tests._fakes import build_coordinator, make_state
    from tests._ha_stubs import install_stubs
    install_stubs()
    from sboom_ha.iio_client import IioCapability
    from sboom_ha.sensor import HW_SENSOR_SPECS
    coord = build_coordinator(state=make_state())
    coord.iio_cap = IioCapability(thermal_device="hwmon0")  # только температура
    avail = {s.key for s in HW_SENSOR_SPECS if s.available_fn(coord)}
    assert avail == {"soc_temperature"}


# Реальный захват `matter list` с колонки (одно устройство). Заголовок
# таблицы слова «matter» НЕ содержит — регресс, из-за которого probe ложно
# отключал сенсор при подключённом Matter-устройстве.
_MATTER_TBL = (
    "| NodeId | XID | Serial number | Model ID | Can report RSSI | RSSI |\n"
    "|------------------|----------------------|-----------|------------------|-----------------|------|\n"
    "| 0000B81601A8B0D3 | cingsma4finr81daq3a0 | FCH1000237 | MTFFF40002 | yes | -55 |\n"
)


def test_parse_matter_list_real_device():
    """Реальная таблица → одно устройство; заголовок и разделитель отсечены."""
    devs = parse_matter_list(_MATTER_TBL)
    assert len(devs) == 1
    d = devs[0]
    assert d.node_id == "0000B81601A8B0D3"  # заголовок 'NodeId' (не hex) не попал
    assert d.model == "MTFFF40002"
    assert d.serial == "FCH1000237"
    assert d.rssi == -55


def test_matter_count_real_table_and_empty():
    """Заголовок НЕ считается устройством (регресс: было 2 вместо 1)."""
    assert matter_count(_MATTER_TBL) == 1
    assert matter_count("Matter device list is empty") == 0
    assert matter_count("") == 0


@pytest.mark.asyncio
async def test_matter_probe_accepts_real_device_table():
    """КОРЕНЬ РЕГРЕССА: probe должен принять таблицу устройства (без слова 'matter').

    Раньше проверялось `"matter" in resp` → с подключённым устройством probe
    возвращал False, has_matter_cli становился False, сенсор пропадал.
    """
    c = Cli4242Client("host")
    c._run = AsyncMock(return_value=_MATTER_TBL)
    assert await c.async_matter_probe() is True


@pytest.mark.asyncio
async def test_matter_probe_rejects_missing_command_and_none():
    """Нет matter-подкоманды (error-вывод CLI) или нет ответа → False."""
    c = Cli4242Client("host")
    c._run = AsyncMock(return_value="Not found: matter list\nValid commands:\n  zigbee")
    assert await c.async_matter_probe() is False
    c._run = AsyncMock(return_value=None)
    assert await c.async_matter_probe() is False
