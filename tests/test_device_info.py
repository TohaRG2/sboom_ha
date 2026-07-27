"""Тесты DeviceInfo: firmware/serial_number/host пробрасываются в карточку устройства HA."""
from __future__ import annotations

from sboom_ha.const import DOMAIN
from sboom_ha.media_player import SboomMediaPlayer

from tests._fakes import build_coordinator, make_entry, make_state, make_track


def test_device_info_full_zeroconf_data():
    """Когда все поля присутствуют (zeroconf-flow), DeviceInfo заполнен полностью."""
    entry = make_entry(
        host="10.0.0.42",
        device_id="serial-abc-123",
        device_name="Спальня",
        device_model="sberboom-r2",
        device_firmware="1.234.56",
    )
    coord = build_coordinator(entry=entry, track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, entry)
    di = mp._attr_device_info

    assert di.identifiers == {(DOMAIN, "serial-abc-123")}
    assert di.name == "Спальня"
    assert di.manufacturer == "SberDevices"
    assert di.model == "sberboom-r2"
    assert di.sw_version == "1.234.56"
    assert di.serial_number == "serial-abc-123"
    # configuration_url не задаётся: веб-интерфейса у колонки нет,
    # ссылка вела бы на несуществующую страницу (находка ревью UI/UX).
    assert not di.configuration_url


def test_device_info_manual_flow_partial():
    """Manual flow без zeroconf: device_id/firmware/model/name = None — DeviceInfo с пропусками."""
    entry = make_entry(
        host="10.0.0.99",
        device_id=None,
        device_name="",
        device_model="",
        device_firmware=None,
    )
    # Эмулируем manual flow (None в data)
    entry.data["device_id"] = None
    entry.data["device_name"] = None
    entry.data["device_model"] = None
    entry.data["device_firmware"] = None

    coord = build_coordinator(entry=entry, track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, entry)
    di = mp._attr_device_info

    # identifiers идут от device_id, fallback на host
    assert di.identifiers == {(DOMAIN, "10.0.0.99")}
    # name fallback'ится на f"SberBoom {host}"
    assert di.name == "SberBoom 10.0.0.99"
    # model fallback'ится на дефолт
    assert di.model == "SberBoom"
    # sw_version и serial_number отсутствуют
    assert di.sw_version is None
    assert di.serial_number is None
    assert not di.configuration_url


def test_device_info_serial_equals_device_id():
    entry = make_entry(device_id="MY-SERIAL-XYZ")
    coord = build_coordinator(entry=entry, track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, entry)
    assert mp._attr_device_info.serial_number == "MY-SERIAL-XYZ"


def test_device_info_firmware_passthrough():
    entry = make_entry(device_firmware="9.9.9")
    coord = build_coordinator(entry=entry, track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, entry)
    assert mp._attr_device_info.sw_version == "9.9.9"
