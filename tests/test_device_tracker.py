"""Тесты device_tracker: координаты из location-подсистемы GET_STATE."""
from __future__ import annotations

from tests._fakes import build_coordinator, make_state
from tests._ha_stubs import install_stubs

install_stubs()

from sboom_ha._models import DeviceState  # noqa: E402
from sboom_ha.device_tracker import SboomDeviceTracker  # noqa: E402


def _tracker_with_location(**loc):
    state = make_state()
    state.device = DeviceState(**loc)
    coord = build_coordinator(state=state)
    return SboomDeviceTracker(coord, coord.entry)


def test_tracker_reports_coordinates():
    t = _tracker_with_location(
        latitude=55.660927, longitude=37.469685, location_accuracy=8, location_source="wifi"
    )
    assert t.latitude == 55.660927
    assert t.longitude == 37.469685
    assert t.location_accuracy == 8
    assert t.source_type.value == "gps"


def test_tracker_accuracy_defaults_to_zero_when_unknown():
    """HA ждёт int от location_accuracy: нет данных → 0, а не None (иначе TypeError)."""
    t = _tracker_with_location(latitude=55.6, longitude=37.4)
    assert t.location_accuracy == 0


def test_tracker_none_coordinates_when_no_location():
    """Колонка не прислала location → координаты None (точка не ставится на карту)."""
    coord = build_coordinator(state=make_state())
    t = SboomDeviceTracker(coord, coord.entry)
    assert t.latitude is None and t.longitude is None
    assert t.location_accuracy == 0


def test_tracker_none_coordinates_when_no_state():
    """Нет state вовсе (до первого poll) → безопасные None/0, без падения."""
    coord = build_coordinator(state=None)
    t = SboomDeviceTracker(coord, coord.entry)
    assert t.latitude is None
    assert t.location_accuracy == 0


def test_tracker_disabled_by_default():
    """Координаты — чувствительные данные, entity выключена по умолчанию."""
    assert SboomDeviceTracker._attr_entity_registry_enabled_default is False
