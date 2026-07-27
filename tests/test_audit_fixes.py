"""Тесты на фиксы из code-аудита: герметичность stub'ов, обёртка команд,
поведение при обрыве WS, устойчивость _handle_event."""
from __future__ import annotations

import asyncio
import sys

import pytest

from tests._fakes import build_coordinator, make_state, make_track
from tests._ha_stubs import install_stubs

install_stubs()

from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from sboom_ha._entity_base import SboomEntity  # noqa: E402
from sboom_ha.api import SberSpeakerClient  # noqa: E402

# ─────────────────────── герметичность stub'ов ───────────────────────

def test_installed_stub_carries_marker():
    """install_stubs ставит маркер — по нему отличается наш stub от настоящего HA."""
    assert getattr(sys.modules["homeassistant"], "_SBOOM_STUB", False) is True


def test_install_stubs_idempotent():
    """Повторный вызов с уже стоящими нашими stub'ами — no-op, без исключения."""
    install_stubs()  # не должно бросить


# ─────────────────────── SboomEntity._run_command ───────────────────────

def _entity():
    coord = build_coordinator(track=make_track(), state=make_state())
    return SboomEntity(coord, coord.entry)


@pytest.mark.asyncio
async def test_run_command_wraps_generic_error_in_homeassistanterror():
    """Сырой RuntimeError от WS-клиента оборачивается в HomeAssistantError."""
    ent = _entity()

    async def _boom() -> None:
        raise RuntimeError("not connected")

    with pytest.raises(HomeAssistantError) as exc_info:
        await ent._run_command(_boom(), action="play")

    err = exc_info.value
    assert err.translation_key == "command_failed"
    assert err.translation_placeholders["action"] == "play"
    assert "not connected" in err.translation_placeholders["error"]
    assert isinstance(err.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_run_command_passes_homeassistanterror_through():
    """Уже-HomeAssistantError не оборачивается повторно."""
    ent = _entity()
    original = HomeAssistantError("original")

    async def _raise_ha() -> None:
        raise original

    with pytest.raises(HomeAssistantError) as exc_info:
        await ent._run_command(_raise_ha(), action="pause")

    assert exc_info.value is original


@pytest.mark.asyncio
async def test_run_command_success_no_raise():
    """Успешная команда проходит без исключения."""
    ent = _entity()
    ran = []

    async def _ok() -> None:
        ran.append(True)

    await ent._run_command(_ok(), action="next")
    assert ran == [True]


# ─────────────────────── coordinator: poll при обрыве ───────────────────────

@pytest.mark.asyncio
async def test_async_update_data_skips_poll_when_disconnected():
    """Свежий клиент не подключён (disconnected.set()) — poll отдаёт last-known
    state, не дёргая мёртвый сокет."""
    track = make_track(title="Cached")
    state = make_state(volume=33)
    coord = build_coordinator(track=track, state=state)
    assert coord.client.disconnected.is_set()  # до connect() — disconnected

    data = await coord._async_update_data()

    assert data == {"state": state, "track": track}


# ─────────────────────── api: обрыв listen-loop ───────────────────────

class _ClosingWS:
    """Фейковый WS: при первом же чтении кидает OSError (обрыв соединения)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise OSError("connection reset")


@pytest.mark.asyncio
async def test_listen_loop_sets_disconnected_and_cancels_pending():
    """При обрыве listen-loop выставляет disconnected и снимает pending-futures."""
    client = SberSpeakerClient(host="192.0.2.10", client_id="x")
    client._disconnected.clear()
    client._ws = _ClosingWS()

    loop = asyncio.get_running_loop()
    pending = loop.create_future()
    client._pending["req-1"] = pending

    await client._listen_loop()

    assert client.disconnected.is_set()
    assert not client._pending  # очищено
    assert pending.done()
    with pytest.raises(ConnectionError):
        pending.result()


# ─────────────────────── coordinator._handle_event устойчивость ───────────────────────

@pytest.mark.asyncio
async def test_handle_event_ignores_malformed_payload():
    """field 5 не словарь — _handle_event не падает и не шлёт событий."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.hass.bus.fired.clear()

    await coord._handle_event(b"", {5: "not-a-dict"})
    await coord._handle_event(b"", {})
    await coord._handle_event(b"", {2: "some-id"})

    assert coord.hass.bus.fired == []
