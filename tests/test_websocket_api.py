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


# ────────────────── _serialize_track: обложка и часы (аудит #35/#36) ──────


def test_serialize_track_uses_fallback_cover_for_bt_radio():
    """Для BT/радио cover_url(track) пуст — панель получает найденную обложку,
    как это уже делают media_player и camera."""
    from sboom_ha.websocket_api import _serialize_track

    from tests._fakes import make_track

    track = make_track(provider=None, release_id=None, artist_ids=[])
    data = _serialize_track(track, "https://found.example/cover.jpg")
    assert data["cover_url"] == "https://found.example/cover.jpg"


def test_serialize_track_prefers_catalog_cover():
    from sboom_ha.websocket_api import _serialize_track

    from tests._fakes import make_track

    track = make_track(provider="zvuk", release_id="200")
    data = _serialize_track(track, "https://found.example/cover.jpg")
    assert "cdn-image.zvuk.com" in data["cover_url"]


def test_serialize_track_exposes_received_ts_ms():
    """Панель экстраполирует позицию от часов HA (received_ts), а не от часов
    колонки (position_ts_ms) — тот же класс бага, что чинили в media_player."""
    from sboom_ha.websocket_api import _serialize_track

    from tests._fakes import make_track

    track = make_track()
    track.received_ts = 1700000000.5
    data = _serialize_track(track, None)
    assert data["received_ts_ms"] == 1700000000500
