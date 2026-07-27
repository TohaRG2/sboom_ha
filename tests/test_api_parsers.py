"""Тесты парсеров payload-форматов трека.

Покрываемые кейсы:
- Парсер находит trackId внутри произвольно вложенного JSON
- Артисты собираются и в push-формате (artists[].name), и в state-формате (artists[].title)
- Releases в обоих форматах (name vs title)
- position как dict {tsMs, val} (push) и как int (state)
- Провайдер не zvuk → release_id всё равно может быть
- Mute/volume парсится из state JSON
"""
from __future__ import annotations

from sboom_ha._tlv import field
from sboom_ha.api import SberSpeakerClient

# ─────────────────────── parse_track ───────────────────────

def test_parse_track_push_format(real_track_money_raw):
    """Push-формат: плоский JSON с trackId на верхнем уровне."""
    track = SberSpeakerClient.parse_track(real_track_money_raw)
    assert track is not None
    assert track.track_id == "1001"
    assert track.title == "Test Track"
    assert track.artists == ["Test Artist"]
    assert track.album == "Test Album"
    assert track.release_id == "200"
    assert track.provider == "zvuk"
    assert track.playing is True
    assert track.position_sec == 184
    assert track.position_ts_ms == 1700000000000
    assert track.shuffle is False
    assert track.repeat == "none"


def test_parse_track_state_format_with_info_wrapper(real_track_state_format_raw):
    """State-формат: трек завёрнут в `info{player{...}}` с другими ключами полей."""
    track = SberSpeakerClient.parse_track(real_track_state_format_raw)
    assert track is not None
    assert track.title == "Test Track 2"
    assert track.artists == ["Test Artist 2"]
    # В state-формате releases имеют "title", не "name" — парсер должен это понять
    assert track.album == "Test Album 2"


def test_parse_track_returns_none_for_garbage():
    """Когда нет trackId — парсер должен вернуть None, не падать."""
    assert SberSpeakerClient.parse_track(b"random binary garbage no json") is None
    assert SberSpeakerClient.parse_track(b"") is None
    # JSON без trackId
    assert SberSpeakerClient.parse_track(b'{"foo":"bar"}') is None


def test_parse_track_handles_empty_artists_array():
    """Бывает что artists пуст (например, для radio/podcast)."""
    raw = b'{"trackId":"42","title":"X","artists":[],"provider":"zvuk"}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.artists == []
    assert track.title == "X"


def test_parse_track_handles_missing_release_id():
    """Без release_id — парсер не должен падать, просто release_id=None."""
    raw = b'{"trackId":"42","title":"X","artists":[{"name":"A"}],"provider":"salute"}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.release_id is None


def test_parse_track_extracts_playback_speed(real_track_money_raw):
    """playbackSpeedRate из метаданных трека → TrackInfo.playback_speed (float)."""
    track = SberSpeakerClient.parse_track(real_track_money_raw)
    assert track is not None
    assert track.playback_speed == 1.0


def test_parse_track_extracts_non_default_playback_speed():
    """Колонка играет на ускорении — playback_speed != 1.0."""
    raw = b'{"trackId":"42","title":"X","artists":[],"playbackSpeedRate":1.5}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.playback_speed == 1.5


def test_parse_track_playback_speed_none_when_absent():
    """Нет playbackSpeedRate в payload — playback_speed остаётся None."""
    raw = b'{"trackId":"42","title":"X","artists":[]}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.playback_speed is None


def test_parse_track_trackid_in_middle_of_large_payload():
    """Реальный кейс: trackId глубоко в большом payload — бинарный префикс,
    посторонний закрытый JSON-объект до трека и мусорный хвост после."""
    raw = (
        b"\x00\x04\xba\x01 binary prefix "
        b'{"volume":{"muted":false,"percent":10}} '
        b'{"trackId":"55","title":"Mid Track",'
        b'"artists":[{"id":"7","name":"Artist Mid"}],'
        b'"playing":true,"provider":"zvuk"}'
        b"\xff\xfe trailing garbage"
    )
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.track_id == "55"
    assert track.title == "Mid Track"
    assert track.artists == ["Artist Mid"]
    assert track.playing is True


def test_parse_track_state_format_status_from_player_wrapper():
    """State-формат: position/shuffle/timestamp берутся из окружающего player{},
    а не из info{} с треком (info попадает в backward-скан по trackId)."""
    raw = (
        b"\x00\x02prefix"
        b'{"info":{"player":{'
        b'"playing":true,"position":42,"shuffle":true,'
        b'"stateChangedTimestamp":1700000001234,'
        b'"info":{"trackId":"777","title":"Deep Track",'
        b'"artists":[{"id":"3","name":"Deep Artist"}],"duration":200}}}}'
    )
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.track_id == "777"
    assert track.playing is True
    assert track.shuffle is True
    assert track.position_sec == 42
    # timestamp позиции нет в state-формате — берётся stateChangedTimestamp
    assert track.position_ts_ms == 1700000001234
    assert track.duration_sec == 200


# ─────────────────────── parse_queue ───────────────────────


def test_parse_queue_extracts_track_ids(queue_raw):
    """op=17: очередь — упорядоченный список trackId + флаг explicit."""
    queue = SberSpeakerClient.parse_queue(queue_raw)
    assert len(queue) == 6
    assert queue[0].track_id == "112774234"
    assert queue[0].explicit is False
    assert [q.track_id for q in queue] == [
        "112774234", "112774241", "112774249",
        "112774276", "112774292", "112774302",
    ]


def test_parse_queue_preserves_explicit_flag():
    arr = b'[{"explicit":true,"trackId":5},{"explicit":false,"trackId":6}]'
    # реальный op=17 всегда содержит поле 4 (бинарь) перед полем 5 — см. exp_23
    inner = field(4, 2, b"\x04\x05") + field(5, 2, arr)
    raw = field(5, 2, field(17, 2, inner))
    queue = SberSpeakerClient.parse_queue(raw)
    assert queue[0].explicit is True
    assert queue[1].explicit is False


def test_parse_queue_empty_array():
    raw = field(5, 2, field(17, 2, field(5, 2, b"[]")))
    assert SberSpeakerClient.parse_queue(raw) == []


def test_parse_queue_broken_payload_returns_empty():
    """Мусор / пустой / отсутствующее поле — пустой список, не падение."""
    assert SberSpeakerClient.parse_queue(b"random garbage") == []
    assert SberSpeakerClient.parse_queue(b"") == []
    # envelope без вложенного op=17
    assert SberSpeakerClient.parse_queue(field(5, 2, field(99, 2, b""))) == []


# ─────────────────────── parse_state ───────────────────────

def test_parse_state_extracts_volume_and_mute():
    """GetState возвращает volume из nested JSON. Парсер находит его regex'ом."""
    raw = (
        b'\x00\x00 some prefix bytes '
        b'{"volume":{"muted":false,"percent":42}, "other":"data"}'
        b' suffix bytes'
    )
    state = SberSpeakerClient.parse_state(raw)
    assert state.volume_percent == 42
    assert state.muted is False


def test_parse_state_handles_muted_true():
    raw = b'{"volume":{"muted":true,"percent":0}}'
    state = SberSpeakerClient.parse_state(raw)
    assert state.muted is True
    assert state.volume_percent == 0


def test_parse_state_returns_none_on_unparseable_payload():
    """Полностью нераспознанный payload → None, а НЕ state с нулями.

    Регрессия из ревью: раньше возвращался SpeakerState(volume=0, muted=False),
    и один битый push обнулял громкость и все device-сенсоры в UI.
    """
    assert SberSpeakerClient.parse_state(b"no volume here") is None


def test_parse_state_json_without_volume_keeps_fields_unknown():
    """JSON распознан, но volume-блока нет → volume/muted остаются None
    (coordinator домерджит их из прежнего state), а не обнуляются."""
    state = SberSpeakerClient.parse_state(b'{"alarm":{"alarmsCounter":0}}')
    assert state is not None
    assert state.volume_percent is None
    assert state.muted is None


def test_parse_state_keeps_raw_json_chunk():
    """raw_state_json должен сохраниться для отладки."""
    raw = b'{"volume":{"muted":false,"percent":50}}'
    state = SberSpeakerClient.parse_state(raw)
    assert state.raw_state_json is not None
    assert "volume" in state.raw_state_json


# ─────────────────────── parse_paired_bt / parse_scanned_bt ───────────────────────

# Пустой ответ op=21 (status=1, устройств нет) — снят с колонки, без PII.
_OP21_EMPTY_SAMPLE = bytes.fromhex(
    "0802122439393039386237392d646233662d346565362d626363652d313736323163"
    "3266653261342a05aa01020801"
)


def _paired_dev(mac: str, name: str, connected: bool) -> bytes:
    return (field(1, 2, mac.encode()) + field(2, 2, name.encode())
            + field(3, 0, 1 if connected else 0))


def test_parse_paired_bt_multiple_devices():
    """Парсер op=19 на реальной wire-структуре (envelope→5→19→repeated field 1)."""
    inner = (field(1, 2, _paired_dev("AA:BB:CC:00:11:22", "Phone", True))
             + field(1, 2, _paired_dev("DD:EE:FF:33:44:55", "Headset", False)))
    raw = field(5, 2, field(19, 2, inner))
    devices = SberSpeakerClient.parse_paired_bt(raw)
    assert [d.mac for d in devices] == ["AA:BB:CC:00:11:22", "DD:EE:FF:33:44:55"]
    assert [d.name for d in devices] == ["Phone", "Headset"]
    assert devices[0].connected is True
    assert devices[1].connected is False


def test_parse_paired_bt_empty():
    raw = field(5, 2, field(19, 2, b""))
    assert SberSpeakerClient.parse_paired_bt(raw) == []
    assert SberSpeakerClient.parse_paired_bt(b"garbage") == []


def test_parse_scanned_bt_empty_sample():
    """op=21: status=1, repeated-устройств нет → []."""
    assert SberSpeakerClient.parse_scanned_bt(_OP21_EMPTY_SAMPLE) == []


def test_parse_scanned_bt_with_devices():
    dev = field(1, 2, b"11:22:33:44:55:66") + field(2, 2, b"Speaker") + field(3, 0, 200)
    # ScannedDevice — field 2 (repeated) внутри ответа; field 1 = status
    inner = field(1, 0, 1) + field(2, 2, dev)
    raw = field(5, 2, field(21, 2, inner))
    devices = SberSpeakerClient.parse_scanned_bt(raw)
    assert len(devices) == 1
    assert devices[0].mac == "11:22:33:44:55:66"
    assert devices[0].name == "Speaker"
    assert devices[0].rssi == 200


def test_parse_track_extracts_has_lyrics():
    """info.hasLyrics → track.has_lyrics (схема из реального metadata-захвата)."""
    raw = b'{"trackId":"42","title":"X","artists":[],"hasLyrics":true}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None and track.has_lyrics is True

    raw2 = b'{"trackId":"42","title":"X","artists":[],"hasLyrics":false}'
    assert SberSpeakerClient.parse_track(raw2).has_lyrics is False


def test_parse_track_has_lyrics_none_when_absent():
    """Нет hasLyrics — поле остаётся None (не False): 'неизвестно' ≠ 'нет текста'."""
    raw = b'{"trackId":"42","title":"X","artists":[]}'
    assert SberSpeakerClient.parse_track(raw).has_lyrics is None


def test_parse_track_rich_now_playing_fields():
    """Богатый Now Playing из реального live-захвата (tests/fixtures/track_metadata.json):
    playlist/type/id/source/childMode/playingPending парсятся."""
    from pathlib import Path
    raw = (Path(__file__).parent / "fixtures" / "track_metadata.json").read_bytes()
    t = SberSpeakerClient.parse_track(raw)
    assert t is not None
    assert t.playlist_title == "Персональная волна"
    assert t.playlist_type == "endless"
    assert t.playlist_id == "9999"
    assert t.media_source == "MUSIC"
    assert t.playlist_liked is False
    assert t.child_mode is False
    assert t.buffering is False


def test_parse_track_rich_fields_none_when_absent():
    """Минимальный payload — новые поля None (не False), «неизвестно» ≠ «выключено»."""
    t = SberSpeakerClient.parse_track(b'{"trackId":"1","title":"X","artists":[]}')
    assert t.playlist_type is None
    assert t.media_source is None
    assert t.child_mode is None
    assert t.buffering is None
