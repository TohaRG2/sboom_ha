"""Pytest fixtures для тестов sboom_ha."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Добавляем custom_components в sys.path чтобы можно было импортировать sboom_ha
# напрямую без HA-stub (часть тестов работает без HA окружения).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))

# Регистрируем HA-stubs ДО любого импорта sboom_ha.* в тестах.
# Тесты, которые импортируют только api/helpers/lyrics_client/image_render —
# не используют HA и stubs им не мешают; тесты на media_player/sensor/etc. —
# нуждаются в stubs.
sys.path.insert(0, str(ROOT))
from tests._ha_stubs import install_stubs  # noqa: E402

install_stubs()


import pytest  # noqa: E402  — stubs должны установиться до HA-импортов


@pytest.fixture
def real_track_money_raw() -> bytes:
    """Синтетический payload в push-формате (плоский JSON с trackId)."""
    json_part = (
        b'{"artists":[{"id":"1","name":"Test Artist"}],'
        b'"childMode":false,"duration":0,"explicit":true,"like":false,'
        b'"mediaSource":"MUSIC","playbackSpeedRate":1.0,'
        b'"playing":true,"playingPending":false,'
        b'"playlistId":"100","playlistLike":false,'
        b'"playlistTitle":"Test Playlist","playlistType":"endless",'
        b'"position":{"tsMs":1700000000000,"val":184},'
        b'"provider":"zvuk",'
        b'"releases":[{"id":"200","name":"Test Album"}],'
        b'"repeatType":"none","shuffle":false,'
        b'"stateChangedTimestamp":1699999990000,'
        b'"title":"Test Track","trackId":"1001"}'
    )
    # Эмулируем wrapping: parse_track ищет JSON по trackId, любой "хвост" игнорируется.
    return b"\x00\x00" + json_part + b"\x00\x00"


@pytest.fixture
def real_track_state_format_raw() -> bytes:
    """Синтетический payload в state-формате (info{} обертка с другими названиями полей)."""
    return (
        b'{"info":{"player":{'
        b'"artists":[{"id":"2","name":"Test Artist 2"}],'
        b'"playing":true,'
        b'"position":120,'
        b'"duration":328,'
        b'"provider":"zvuk",'
        b'"releases":[{"id":"300","title":"Test Album 2"}],'
        b'"title":"Test Track 2",'
        b'"trackId":"2002"}}}'
    )


@pytest.fixture
def device_state_raw() -> bytes:
    """Синтетический GET_STATE-payload — структура реального дампа без PII.

    Бинарные префикс/суффикс имитируют сырой payload колонки (parse_state
    извлекает сбалансированный JSON-объект, мусор по краям игнорируется).
    """
    state = {
        "alarm": {
            "alarms": [], "alarmsCounter": 0, "clocks": [],
            "playing": None, "status": 1, "timers": [],
        },
        "assistant": {"auto_volume": False, "character": "afina"},
        "background_apps": [
            {"app_info": {"systemName": "music",
                          "frontendEndpoint": "ru.sberdevices.music"},
             "state": {"player": {"playing": True}}},
            {"app_info": {"systemName": "voice_auth"}, "state": {}},
        ],
        "capabilities_state": {"led_display": {"brightness": 100, "turned_on": True}},
        "current_app": {"app_info": {}, "state": {}},
        "deviceSleep": {"systemState": "working"},
        "homeSecurity": {"enabled": False},
        "locale": {"locale": "ru_RU"},
        "morning_show": {"from_show": False, "in_show": False},
        "multiroom": {"enabled": True, "mode": "NONE", "stereoPair": {"active": False}},
        "network": {"connection_type": "WIFI", "ip": "0.0.0.0"},
        "proactivityNotification": {"hasNotification": False},
        "subscrDeviceInfo": {"isSubscrDevice": False},
        "volume": {"muted": False, "percent": 3},
    }
    return b"\x00\x00" + json.dumps(state).encode() + b"\x00\xff"


@pytest.fixture
def queue_raw() -> bytes:
    """Синтетический ответ op=17 (очередь) — формат как в research/exp_23.

    Envelope {1:2, 2:rid, 5:{17:{4:…, 5:<JSON-массив>, 6:0}}}.
    """
    from sboom_ha._tlv import field

    arr = json.dumps([
        {"explicit": False, "trackId": tid}
        for tid in (112774234, 112774241, 112774249, 112774276, 112774292, 112774302)
    ])
    inner = field(4, 2, bytes.fromhex("0405")) + field(5, 2, arr.encode()) + field(6, 0, 0)
    body = field(17, 2, inner)
    return field(1, 0, 2) + field(2, 2, b"req-queue") + field(5, 2, body)


@pytest.fixture
def lrclib_track_response() -> dict:
    """Типичный ответ Lrclib.net (публичный API) для синхронизированных lyrics."""
    return {
        "id": 1,
        "trackName": "Test Track",
        "artistName": "Test Artist",
        "albumName": "Test Album",
        "duration": 313.0,
        "instrumental": False,
        "plainLyrics": "Hey, hey, you there\nTell me how have you been?",
        "syncedLyrics": (
            "[00:11.20]Hey, hey, you there\n"
            "[00:13.50]Tell me how have you been?\n"
            "[00:18.00]You could have a steam train\n"
            "[02:15.40]I wanna be your sledgehammer"
        ),
    }


@pytest.fixture
def lrclib_instrumental_response() -> dict:
    """Lrclib для инструментальной композиции — без текста."""
    return {
        "id": 1,
        "trackName": "Some Instrumental",
        "artistName": "Composer",
        "instrumental": True,
        "plainLyrics": None,
        "syncedLyrics": None,
    }
