"""Тесты System Health: метрики для Settings → System → System Information."""
from __future__ import annotations

import pytest
from sboom_ha.const import DOMAIN
from sboom_ha.system_health import system_health_info

from tests._fakes import build_coordinator, make_state, make_track
from tests._ha_stubs import HomeAssistant


@pytest.mark.asyncio
async def test_health_zero_speakers():
    hass = HomeAssistant()
    info = await system_health_info(hass)
    assert info["configured_speakers"] == 0
    assert info["connected_speakers"] == 0
    assert info["lrclib_reachable"] == "ok"


@pytest.mark.asyncio
async def test_health_one_speaker_connected():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord.hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord

    info = await system_health_info(coord.hass)
    assert info["configured_speakers"] == 1
    assert info["connected_speakers"] == 1


@pytest.mark.asyncio
async def test_health_one_speaker_disconnected():
    coord = build_coordinator(track=make_track(), state=make_state())
    # connected=False по дефолту
    coord.hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord

    info = await system_health_info(coord.hass)
    assert info["configured_speakers"] == 1
    assert info["connected_speakers"] == 0


@pytest.mark.asyncio
async def test_health_lrclib_url_returns_ok_in_stub():
    """Sanity-check: stub возвращает 'ok' для async_check_can_reach_url."""
    hass = HomeAssistant()
    info = await system_health_info(hass)
    assert info["lrclib_reachable"] == "ok"
