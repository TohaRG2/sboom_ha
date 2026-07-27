"""Тесты custom services: refresh_metadata, reauth."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sboom_ha.const import DOMAIN
from sboom_ha.services import (
    SERVICE_REAUTH,
    SERVICE_REFRESH_METADATA,
    async_register_services,
)

from tests._fakes import build_coordinator, make_state, make_track
from tests._ha_stubs import HomeAssistant, ServiceCall


def _setup_hass_with_coord(coord) -> HomeAssistant:
    # build_coordinator уже прописал entry.runtime_data и config_entries.
    return coord.hass


# ─────────────────── registration ───────────────────

def test_register_services_idempotent():
    hass = HomeAssistant()
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA)
    assert hass.services.has_service(DOMAIN, SERVICE_REAUTH)
    # Повторный вызов не падает
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA)


# ─────────────────── refresh_metadata ───────────────────

@pytest.mark.asyncio
async def test_refresh_metadata_calls_request_refresh_on_all_when_no_target():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.async_request_refresh = AsyncMock()
    hass = _setup_hass_with_coord(coord)
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REFRESH_METADATA)]
    await handler(ServiceCall(domain=DOMAIN, service=SERVICE_REFRESH_METADATA, data={}))

    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_metadata_propagates_errors_to_caller():
    """Новый контракт: ошибка команды видна пользователю, а не глотается молча."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.async_request_refresh = AsyncMock(side_effect=RuntimeError("WS dead"))
    hass = _setup_hass_with_coord(coord)
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REFRESH_METADATA)]
    with pytest.raises(RuntimeError):
        await handler(ServiceCall(data={}))


@pytest.mark.asyncio
async def test_service_call_without_loaded_entries_raises_validation_error():
    """Без загруженных entries сервис падает с ServiceValidationError
    (IQS action-setup), а не молча делает ничего."""
    from tests._ha_stubs import ServiceValidationError

    hass = HomeAssistant()
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REFRESH_METADATA)]
    with pytest.raises(ServiceValidationError):
        await handler(ServiceCall(data={}))


# ─────────────────── reauth ───────────────────

@pytest.mark.asyncio
async def test_reauth_triggers_async_start_reauth():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.entry.async_start_reauth = MagicMock()
    hass = _setup_hass_with_coord(coord)
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REAUTH)]
    await handler(ServiceCall(data={}))

    coord.entry.async_start_reauth.assert_called_once_with(hass)
