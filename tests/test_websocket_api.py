"""Тесты helper'ов WebSocket API панели.

Проверяем чистые функции сборки/разбора deeplink — контракт tid vs pid и
разбор zvuk-URL, на который завязаны панель (sboom/play) и сервис play_music.
Импорт модуля идёт через HA-стабы (conftest).
"""
from __future__ import annotations

import pytest
from sboom_ha.websocket_api import _build_deeplink, _deeplink_from_zvuk_url


@pytest.mark.parametrize(
    ("pt", "item_id", "expected"),
    [
        ("track", "1", "staros://music?tid=1&pt=track"),
        ("podcast", "2", "staros://music?tid=2&pt=podcast"),
        ("artist", "3", "staros://music?pid=3&pt=artist"),
        ("release", "4", "staros://music?pid=4&pt=release"),
        ("playlist", "5", "staros://music?pid=5&pt=playlist"),
    ],
)
def test_build_deeplink_tid_vs_pid(pt, item_id, expected):
    assert _build_deeplink(pt, item_id) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://zvuk.com/track/84279897", "staros://music?tid=84279897&pt=track"),
        ("https://zvuk.com/artist/126769660", "staros://music?pid=126769660&pt=artist"),
        ("https://zvuk.com/release/14359015", "staros://music?pid=14359015&pt=release"),
        ("https://zvuk.com/abook/999", "staros://music?tid=999&pt=podcast"),
    ],
)
def test_deeplink_from_zvuk_url(url, expected):
    assert _deeplink_from_zvuk_url(url) == expected


def test_deeplink_from_zvuk_url_invalid():
    assert _deeplink_from_zvuk_url("https://zvuk.com/") is None
    assert _deeplink_from_zvuk_url("https://example.com/unknown/1") is None
