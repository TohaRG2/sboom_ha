"""Единый командный слой coordinator.async_execute (аудит #18).

Один источник политики «команда → вызов клиента → optimistic-патч →
refresh»: media_player, switch/number/select и ws-команды панели — тонкие
адаптеры над ним.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests._fakes import build_coordinator, make_state, make_track


def _coord(**kw):
    coord = build_coordinator(
        track=kw.pop("track", make_track()),
        state=kw.pop("state", make_state(volume=50, muted=False)),
    )
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.mark.asyncio
async def test_execute_mute_applies_optimistic_and_refreshes():
    """Mute не приходит push'ем: optimistic-патч + debounced refresh —
    переключатель больше не «отпрыгивает» (раньше switch патча не делал)."""
    coord = _coord()
    coord.client.media_mute = AsyncMock()
    await coord.async_execute("mute")
    coord.client.media_mute.assert_awaited_once()
    assert coord.state.muted is True
    coord.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_execute_volume_coerces_patches_and_refreshes():
    coord = _coord()
    coord.client.set_volume = AsyncMock()
    await coord.async_execute("volume", "80")
    coord.client.set_volume.assert_awaited_once_with(80)
    assert coord.state.volume_percent == 80
    coord.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_execute_play_patches_track_without_refresh():
    """Play подтверждается push'ем почти мгновенно — refresh не нужен."""
    coord = _coord(track=make_track(playing=False))
    coord.client.media_play = AsyncMock()
    await coord.async_execute("play")
    assert coord.track.playing is True
    coord.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_unknown_action_raises():
    coord = _coord()
    with pytest.raises(ValueError):
        await coord.async_execute("self_destruct")


@pytest.mark.asyncio
async def test_execute_value_required():
    coord = _coord()
    with pytest.raises(ValueError):
        await coord.async_execute("volume")


@pytest.mark.asyncio
async def test_execute_shuffle_patches_track():
    coord = _coord(track=make_track(shuffle=False))
    coord.client.media_shuffle = AsyncMock()
    await coord.async_execute("shuffle", True)
    coord.client.media_shuffle.assert_awaited_once_with(True)
    assert coord.track.shuffle is True
