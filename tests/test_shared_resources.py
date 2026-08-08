"""Жизненный цикл разделяемых ресурсов: ZvukClient (аудит #31) и
подписки панели на координатор (аудит #32)."""
from __future__ import annotations

import pytest

from tests._ha_stubs import HomeAssistant, install_stubs

install_stubs()

import sboom_ha as integration  # noqa: E402
from sboom_ha.const import ZVUK_CLIENT_KEY  # noqa: E402
from sboom_ha.zvuk_client import ZvukClient  # noqa: E402

from tests._fakes import build_coordinator  # noqa: E402

# ────────────────────────── ZvukClient (аудит #31) ──────────────────────────


@pytest.mark.asyncio
async def test_close_zvuk_client_releases_http_pool():
    hass = HomeAssistant()
    client = ZvukClient()
    client._http()  # лениво поднять httpx-клиент
    hass.data[ZVUK_CLIENT_KEY] = client
    await integration._async_close_zvuk_client(hass)
    assert client._client is None, "httpx-пул не закрыт"
    assert ZVUK_CLIENT_KEY not in hass.data


@pytest.mark.asyncio
async def test_close_zvuk_client_noop_when_absent():
    await integration._async_close_zvuk_client(HomeAssistant())  # не падает


def test_zvuk_singleton_shared_between_services_and_ws():
    """services и websocket_api обязаны видеть один и тот же инстанс."""
    from sboom_ha import services, websocket_api

    hass = HomeAssistant()
    c1 = services._zvuk_client(hass)
    c2 = websocket_api._get_zvuk_client(hass)
    assert c1 is c2
    assert hass.data[ZVUK_CLIENT_KEY] is c1


# ─────────────────── stop-подписка координатора (аудит #32) ──────────────────


@pytest.mark.asyncio
async def test_async_stop_notifies_stop_listeners():
    """Панель узнаёт об остановке координатора (reload entry) и может
    переподписаться на новый — иначе подписка замирала на мёртвом инстансе."""
    coord = build_coordinator()
    called: list[bool] = []
    coord.async_add_stop_listener(lambda: called.append(True))

    async def noop() -> None:
        pass

    coord.client.close = noop
    await coord.async_stop()
    assert called == [True]


@pytest.mark.asyncio
async def test_removed_stop_listener_not_called():
    coord = build_coordinator()
    called: list[bool] = []
    remove = coord.async_add_stop_listener(lambda: called.append(True))
    remove()

    async def noop() -> None:
        pass

    coord.client.close = noop
    await coord.async_stop()
    assert called == []
