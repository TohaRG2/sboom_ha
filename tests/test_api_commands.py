"""Тесты бинарного формата исходящих команд к колонке."""
from __future__ import annotations

import struct

import pytest
from sboom_ha._tlv import field
from sboom_ha.api import SberSpeakerClient
from sboom_ha.const import (
    BT_CMD_DISCONNECT,
    OP_BT_DEVICE_COMMAND,
    OP_BT_DISCOVERABLE,
    OP_FIND_REMOTE,
    OP_SET_PLAYBACK_SPEED,
)


class _CapturingWS:
    """Фейковый WS — запоминает все отправленные пакеты, не ходит в сеть."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make_client() -> SberSpeakerClient:
    return SberSpeakerClient(
        host="192.0.2.10", client_id="x", pin_access_token="t" * 16
    )


# ─────────────────────── set_playback_speed ───────────────────────


@pytest.mark.asyncio
async def test_set_playback_speed_uses_float_encoding():
    """op=23 кодирует скорость как float (wire-type 5), НЕ varint.

    varint-кодировка ломает playbackSpeedRate колонки в 0.0 (research exp_22).
    """
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.set_playback_speed(1.5)

    assert len(ws.sent) == 1
    # request_data = field(op=23, field(tag=1, kind=5 float, 1.5))
    inner = field(OP_SET_PLAYBACK_SPEED, 2, b"\x0d" + struct.pack("<f", 1.5))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_set_playback_speed_clamps_above_maximum():
    """Скорость выше 2.0 ограничивается до 2.0."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.set_playback_speed(5.0)

    inner = field(OP_SET_PLAYBACK_SPEED, 2, b"\x0d" + struct.pack("<f", 2.0))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_set_playback_speed_clamps_below_minimum():
    """Скорость ниже 0.5 ограничивается до 0.5 (0.0 — битое состояние колонки)."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.set_playback_speed(0.1)

    inner = field(OP_SET_PLAYBACK_SPEED, 2, b"\x0d" + struct.pack("<f", 0.5))
    assert field(5, 2, inner) in ws.sent[0]


# ─────────────────────── find_remote / bt_make_discoverable ───────────────────────


@pytest.mark.asyncio
async def test_find_remote_sends_op13():
    """op=13 — команда поиска пульта ДУ, пустой inner."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.find_remote()

    assert len(ws.sent) == 1
    inner = field(OP_FIND_REMOTE, 2, field(1, 2, b""))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_bt_make_discoverable_sends_op22():
    """op=22 — режим BT-сопряжения, пустой inner."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.bt_make_discoverable()

    assert len(ws.sent) == 1
    inner = field(OP_BT_DISCOVERABLE, 2, field(1, 2, b""))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_bt_device_command_encodes_mac_and_cmd():
    """op=20 — request {mac (field 1), cmd (field 2 varint)}."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.bt_device_command("AA:BB:CC:00:11:22", BT_CMD_DISCONNECT)

    assert len(ws.sent) == 1
    inner = field(
        OP_BT_DEVICE_COMMAND, 2,
        field(1, 2, b"AA:BB:CC:00:11:22") + field(2, 0, BT_CMD_DISCONNECT),
    )
    assert field(5, 2, inner) in ws.sent[0]
