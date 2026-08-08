"""Тесты helper'ов WebSocket API панели.

Проверяем валидацию входов sboom/play — deeplink из фронтенда, zvuk-URL и
пары pt+id идут на колонку только после проверки (SSRF/инъекции — аудит #41).
Сборка deeplink делегируется ZvukClient (единый источник — аудит #17).
Импорт модуля идёт через HA-стабы (conftest).
"""
from __future__ import annotations

import pytest
from sboom_ha.websocket_api import _is_valid_deeplink


@pytest.mark.parametrize(
    "deeplink",
    [
        "staros://music?tid=84279897&pt=track",
        "staros://music?pid=126769660&pt=artist",
        "staros://radio?station=record",
    ],
)
def test_is_valid_deeplink_accepts_staros(deeplink):
    assert _is_valid_deeplink(deeplink)


@pytest.mark.parametrize(
    "deeplink",
    [
        "https://evil.example/x",  # не staros
        "javascript:alert(1)",
        "staros://music?tid=1 2",  # пробел
        'staros://music?tid="1"',  # кавычки
        "staros://music?tid=1\n2",  # перевод строки
        "",
        "staros://",  # пустой остаток
    ],
)
def test_is_valid_deeplink_rejects_garbage(deeplink):
    assert not _is_valid_deeplink(deeplink)
