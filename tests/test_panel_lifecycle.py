"""Жизненный цикл боковой панели между entries (аудит #14).

Панель существует, пока её хочет хотя бы один загруженный entry
(refcount-семантика): entry с panel_enabled=False не сносит панель чужой
колонки, а выгрузка последнего entry убирает пункт из бокового меню.
"""
from __future__ import annotations

import pytest

from tests._ha_stubs import HomeAssistant, install_stubs

install_stubs()

import sboom_ha as integration  # noqa: E402
from sboom_ha.const import OPT_PANEL_ENABLED  # noqa: E402

from tests._fakes import make_entry  # noqa: E402


@pytest.fixture
def panel_calls(monkeypatch):
    calls = {"registered": 0, "removed": 0}
    monkeypatch.setattr(
        integration, "async_register_built_in_panel",
        lambda *a, **k: calls.__setitem__("registered", calls["registered"] + 1),
    )
    monkeypatch.setattr(
        integration, "async_remove_panel",
        lambda *a, **k: calls.__setitem__("removed", calls["removed"] + 1),
    )
    return calls


def _hass_with(*entries) -> HomeAssistant:
    hass = HomeAssistant()
    for entry in entries:
        hass.config_entries.add(entry)
    return hass


def test_panel_registered_once_for_enabled_entry(panel_calls):
    entry = make_entry(entry_id="a")
    hass = _hass_with(entry)
    integration._sync_panel(hass, assume_loaded=entry)
    integration._sync_panel(hass, assume_loaded=entry)  # идемпотентно
    assert panel_calls == {"registered": 1, "removed": 0}


def test_disabled_entry_does_not_remove_panel_wanted_by_other(panel_calls):
    """Две колонки: у B панель выключена. Перезагрузка B не должна сносить
    панель, которую хочет A."""
    a = make_entry(entry_id="a")
    b = make_entry(entry_id="b")
    b.options = {OPT_PANEL_ENABLED: False}
    hass = _hass_with(a, b)
    integration._sync_panel(hass, assume_loaded=a)
    assert panel_calls["registered"] == 1
    integration._sync_panel(hass, assume_loaded=b)  # setup/reload B
    assert panel_calls["removed"] == 0, "панель A снесена перезагрузкой B"


def test_unload_last_wanting_entry_removes_panel(panel_calls):
    a = make_entry(entry_id="a")
    hass = _hass_with(a)
    integration._sync_panel(hass, assume_loaded=a)
    assert panel_calls["registered"] == 1
    integration._sync_panel(hass, exclude=a)  # unload последнего entry
    assert panel_calls["removed"] == 1
    assert not hass.data.get("sboom_ha_panel_registered")


def test_panel_removed_when_option_disabled_on_single_entry(panel_calls):
    entry = make_entry(entry_id="a")
    hass = _hass_with(entry)
    integration._sync_panel(hass, assume_loaded=entry)
    entry.options = {OPT_PANEL_ENABLED: False}
    integration._sync_panel(hass, assume_loaded=entry)  # reload после options
    assert panel_calls == {"registered": 1, "removed": 1}
