"""Парсеры payload-форматов колонки.

Колонка отдаёт state и track-метаданные как JSON (внутри бинарной TLV-обёртки).
Здесь — функции `parse_state` и `parse_track`, не зависящие от транспорта.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ._models import BluetoothDevice, DeviceState, QueueTrack, SpeakerState, TrackInfo
from ._tlv import decode as _decode_tlv
from ._tlv import decode_repeated as _decode_repeated

_LOGGER = logging.getLogger(__name__)


def _extract_json_object(s: str, start: int) -> str | None:
    """Сбалансированный JSON-объект, начиная с `{` на позиции start (учёт строк)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def parse_device_state(data: dict[str, Any]) -> DeviceState:
    """Извлекает подсистемы устройства из распарсенного JSON GET_STATE.

    Каждый доступ защищён: отсутствующая подсистема → соответствующие поля None.
    """
    ds = DeviceState()

    led = (data.get("capabilities_state") or {}).get("led_display") or {}
    ds.led_brightness = led.get("brightness")
    ds.led_on = led.get("turned_on")

    alarm = data.get("alarm")
    if isinstance(alarm, dict):
        ds.alarms = alarm.get("alarms") or []
        counter = alarm.get("alarmsCounter")
        ds.alarms_count = counter if counter is not None else len(ds.alarms)
        ds.timers = alarm.get("timers") or []
        ds.timers_count = len(ds.timers)
        # alarm.playing: null когда тихо, truthy в момент звонка. Точная форма
        # truthy-значения не подтверждена захватом (все сэмплы — null), поэтому
        # трактуем через bool() — покрывает любой из вариантов.
        if "playing" in alarm:
            ds.alarm_ringing = bool(alarm.get("playing"))

    sleep = data.get("deviceSleep")
    if isinstance(sleep, dict):
        ds.sleep_state = sleep.get("systemState")

    multiroom = data.get("multiroom")
    if isinstance(multiroom, dict):
        ds.multiroom_mode = multiroom.get("mode")
        sp = multiroom.get("stereoPair")
        if isinstance(sp, dict):
            ds.stereo_pair_active = sp.get("active")
            # channelFromConfig/pairDeviceFromConfig — "" когда не в паре.
            ds.stereo_pair_channel = sp.get("channelFromConfig") or None
            ds.stereo_pair_device = sp.get("pairDeviceFromConfig") or None

    # Слой связки устройств (SberCast / device-selector-группы / саундбар).
    # Пуст на одиночной колонке; наполняется при farfield/cast/группировке.
    sbercast = data.get("sbercast")
    if isinstance(sbercast, dict):
        ds.sbercast_enabled = sbercast.get("enabled")
        devs = sbercast.get("devices")
        if isinstance(devs, list):
            ds.sbercast_devices = devs

    groups = data.get("deviceGroups")
    if isinstance(groups, dict):
        ds.soundbar_group = groups.get("soundBar")

    selector = data.get("deviceSelector")
    if isinstance(selector, dict):
        ds.selector_groups = {
            k: v
            for k in ("castGroup", "dsGroup", "roomGroup", "qcGroup")
            if isinstance(v := selector.get(k), list) and v
        }

    # active_app — приложение, которое реально играет (state.player.playing).
    # background_apps — самотасующийся z-order стек, поэтому брать [0] нельзя:
    # сенсор флапал бы каждый poll. Если ничего не играет — active_app=None.
    # app_stack — весь стек имён (порядок = z-order) для атрибутов.
    apps = data.get("background_apps")
    if isinstance(apps, list):
        for app in apps:
            if not isinstance(app, dict):
                continue
            name = (app.get("app_info") or {}).get("systemName")
            if name:
                ds.app_stack.append(name)
            player = (app.get("state") or {}).get("player")
            if ds.active_app is None and isinstance(player, dict) and player.get("playing") is True:
                ds.active_app = name

    assistant = data.get("assistant")
    if isinstance(assistant, dict):
        ds.assistant_character = assistant.get("character")
        if "auto_volume" in assistant:
            ds.assistant_auto_volume = bool(assistant.get("auto_volume"))

    proactivity = data.get("proactivityNotification")
    if isinstance(proactivity, dict) and "hasNotification" in proactivity:
        ds.proactivity_notification = bool(proactivity.get("hasNotification"))

    subscr = data.get("subscrDeviceInfo")
    if isinstance(subscr, dict):
        ds.is_subscription_device = subscr.get("isSubscrDevice")

    network = data.get("network")
    if isinstance(network, dict):
        ds.network_type = network.get("connection_type")
        ds.network_ip = network.get("ip")

    security = data.get("homeSecurity")
    if isinstance(security, dict):
        ds.home_security = security.get("enabled")

    show = data.get("morning_show")
    if isinstance(show, dict):
        ds.in_morning_show = show.get("in_show")
        if "from_show" in show:
            ds.morning_show_from = bool(show.get("from_show"))

    tm = data.get("time")
    if isinstance(tm, dict):
        ds.timezone_id = tm.get("timezone_id")
        off = tm.get("timezone_offset_sec")
        if isinstance(off, int):
            ds.timezone_offset_sec = off

    ts = data.get("timesync")
    if isinstance(ts, dict):
        unixtime = ts.get("unixtime")
        if isinstance(unixtime, (int, float)):
            ds.device_unixtime = float(unixtime)

    settings = data.get("user_settings")
    if isinstance(settings, dict):
        ds.age_mode = settings.get("age_mode")
        if "multi_profile" in settings:
            ds.multi_profile = bool(settings.get("multi_profile"))
        if "enable_child_voice_explicit" in settings:
            ds.child_voice_explicit = bool(settings.get("enable_child_voice_explicit"))

    segments = data.get("device_segments")
    if isinstance(segments, list) and segments:
        ds.firmware_channel = ", ".join(str(s) for s in segments)

    current = data.get("current_app")
    if isinstance(current, dict):
        ds.foreground_app = (current.get("app_info") or {}).get("systemName")

    reminders = data.get("reminders")
    if isinstance(reminders, dict):
        ds.reminders = reminders

    location = data.get("location")
    if isinstance(location, dict):
        lat, lon = location.get("lat"), location.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            ds.latitude = float(lat)
            ds.longitude = float(lon)
        acc = location.get("accuracy")
        if isinstance(acc, (int, float)):
            ds.location_accuracy = round(acc)
        ds.location_source = location.get("source")

    return ds


# Медиа-аппы, чей player несёт now-playing (в порядке приоритета).
_MEDIA_APPS = ("bluetooth_media_control", "music")


def _names(value: Any) -> list[str]:
    """artists/releases: массив [{name}] (GET_STATE/music) ИЛИ строка (BT-формат)."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [x.get("name") for x in value if isinstance(x, dict) and x.get("name")]
    return []


def track_from_state(data: dict[str, Any]) -> TrackInfo | None:
    """Now-playing из GET_STATE — когда get_metadata пуст (радио/Bluetooth: нет trackId).

    Скан background_apps по медиа-аппам; берём app с непустым info
    (title/trackId/stationName). Предпочитаем playing=true, но не игнорируем
    паузу (радио на паузе: title="", но stationName остаётся). Прочие аппы
    со своим player (morning_show и т.п.) не имеют этих полей — не мешают.
    """
    apps = data.get("background_apps")
    if not isinstance(apps, list):
        return None

    candidate: tuple[str, dict, dict] | None = None
    for app in apps:
        if not isinstance(app, dict):
            continue
        name = (app.get("app_info") or {}).get("systemName")
        if name not in _MEDIA_APPS:
            continue
        player = (app.get("state") or {}).get("player")
        if not isinstance(player, dict):
            continue
        info = player.get("info")
        if not isinstance(info, dict):
            continue
        if not (info.get("title") or info.get("trackId") or info.get("stationName")):
            continue  # нет чем идентифицировать медиа-контекст
        if player.get("playing"):
            candidate = (name, player, info)
            break  # играющий — безусловный приоритет
        if candidate is None:
            candidate = (name, player, info)  # запомним паузу, вдруг играющих нет

    if candidate is None:
        return None
    name, player, info = candidate

    ti = TrackInfo(raw=info)
    ti.title = info.get("title") or None
    ti.artists = _names(info.get("artists"))
    albums = _names(info.get("releases"))
    ti.album = albums[0] if albums else None
    ti.playing = bool(player.get("playing"))
    ti.station_name = info.get("stationName") or None
    ti.provider = info.get("provider")
    tid = info.get("trackId")
    ti.track_id = str(tid) if tid else None
    ti.playlist_title = info.get("playlistTitle") or None
    ti.playlist_type = info.get("playlistType")

    dur = player.get("duration") or 0
    ti.duration_sec = int(dur) if dur else None  # 0 (радио) → None: прогресс скрыт
    pos = player.get("position")
    if isinstance(pos, (int, float)):
        ti.position_sec = int(pos)

    # media_source: BT по app/provider, радио по mode/station, иначе из info.
    if name == "bluetooth_media_control" or ti.provider == "bluetooth":
        ti.media_source = "BLUETOOTH"
    elif player.get("mode") == "radio" or ti.station_name:
        ti.media_source = "RADIO"
    else:
        ti.media_source = info.get("mediaSource")
    return ti


def parse_state(raw: bytes) -> SpeakerState | None:
    """Парсит GET_STATE: volume/muted + подсистемы устройства (.device).

    Стратегия: извлечь сбалансированный JSON-объект и распарсить. Если JSON
    битый/частичный — fallback на regex по volume (старое поведение), device=None.

    Возвращает None при полном провале разбора (ни JSON, ни volume-regex) —
    чтобы вызывающий НЕ затирал валидный прежний state дефолтами. Поля
    volume_percent/muted остаются None, если в payload их не было; merge с
    прежним состоянием делает coordinator.
    """
    st = SpeakerState()
    s = raw.decode("utf-8", errors="ignore")
    idx = s.find("{")

    obj = _extract_json_object(s, idx) if idx >= 0 else None
    data: dict[str, Any] | None = None
    if obj is not None:
        try:
            parsed = json.loads(obj)
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            data = None

    if data is not None:
        st.raw_state_json = obj
        volume = data.get("volume")
        if isinstance(volume, dict):
            if "percent" in volume:
                st.volume_percent = int(volume["percent"])
            if "muted" in volume:
                st.muted = bool(volume["muted"])
        st.device = parse_device_state(data)
        return st

    # Fallback: битый JSON — regex по volume, как раньше.
    m = re.search(r'"volume":\s*\{\s*"muted":\s*(true|false)\s*,\s*"percent":\s*(\d+)', s)
    if m:
        st.muted = m.group(1) == "true"
        st.volume_percent = int(m.group(2))
        if idx >= 0:
            st.raw_state_json = s[idx:]
        return st

    return None


def _scan_open_brace_backward(s: str, pos: int) -> int:
    """Backward-скан от pos к ближайшей НЕзакрытой `{` (баланс скобок).

    Возвращает индекс открывающей скобки или -1, если не найдена.
    """
    depth = 0
    for i in range(pos, -1, -1):
        ch = s[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return i
            depth -= 1
    return -1


def _find_track_json(s: str) -> tuple[dict[str, Any], int] | None:
    """Ищет JSON-объект, содержащий `"trackId":"NNN"`.

    Стратегия: regex по trackId → backward-скан к открывающей `{` →
    forward-балансировка (`_extract_json_object`) → json.loads.
    Возвращает (объект, позиция его `{` в s) или None.
    """
    m = re.search(r'"trackId":"\d+"', s)
    if not m:
        return None

    start = _scan_open_brace_backward(s, m.start() - 1)
    if start < 0:
        return None

    obj = _extract_json_object(s, start)
    if obj is None:
        return None
    try:
        data = json.loads(obj)
    except json.JSONDecodeError:
        _LOGGER.debug("track JSON parse failed: %s", obj[:200])
        return None
    if "trackId" not in data:
        return None
    return data, start


def _find_outer_player(s: str, start: int) -> dict[str, Any]:
    """State-формат: ищет окружающий player{} перед позицией start.

    В state-формате трек — это info{} внутри player{}, а "playing",
    "position", "shuffle" лежат уровнем выше — в самом player{}.
    Возвращает распарсенный player{} или {} при любом сбое.
    """
    pm = list(re.finditer(r'"player"\s*:\s*\{', s[:start]))
    if not pm:
        return {}
    player_open = pm[-1].end() - 1  # позиция '{'
    obj = _extract_json_object(s, player_open)
    if obj is None:
        return {}
    try:
        return json.loads(obj)
    except json.JSONDecodeError:
        return {}


def _parse_artists(ti: TrackInfo, data: dict[str, Any]) -> None:
    artists_list = data.get("artists") or []
    ti.artists = [a.get("name") for a in artists_list if a.get("name")]
    ti.artist_ids = [
        str(a.get("id")) for a in artists_list if a.get("id") is not None
    ]


def _parse_release(ti: TrackInfo, data: dict[str, Any]) -> None:
    """releases: ключ названия — "name" (push) или "title" (state)."""
    rels = data.get("releases") or []
    if not rels:
        return
    r0 = rels[0]
    ti.album = r0.get("name") or r0.get("title")
    rel_id = r0.get("id")
    if rel_id is not None:
        ti.release_id = str(rel_id)


def _parse_position(
    ti: TrackInfo, data: dict[str, Any], outer: dict[str, Any]
) -> None:
    """position: push → dict {tsMs, val}; state → int секунды (в outer)."""
    pos_data = data.get("position")
    if isinstance(pos_data, dict):
        pv = pos_data.get("val")
        if pv is not None:
            ti.position_sec = int(pv)
        tsms = pos_data.get("tsMs")
        if tsms is not None:
            ti.position_ts_ms = int(tsms)
    elif isinstance(pos_data, (int, float)):
        ti.position_sec = int(pos_data)
    elif outer:
        opos = outer.get("position")
        if isinstance(opos, (int, float)):
            ti.position_sec = int(opos)
            # для outer (state-формат) timestamp position не приходит,
            # используем stateChangedTimestamp как лучший доступный
            changed = outer.get("stateChangedTimestamp")
            if isinstance(changed, (int, float)):
                ti.position_ts_ms = int(changed)


def _parse_playback_status(
    ti: TrackInfo, data: dict[str, Any], outer: dict[str, Any]
) -> None:
    """Статусные поля: в data (push) или в outer player{} (state)."""
    status_src = data if "playing" in data else outer
    ti.playing = bool(status_src.get("playing", False))
    ti.shuffle = bool(status_src.get("shuffle", False))
    ti.repeat = status_src.get("repeatType")

    # playbackSpeedRate: в push-формате — в data, в state-формате — в player{}
    speed = data.get("playbackSpeedRate")
    if speed is None:
        speed = outer.get("playbackSpeedRate")
    if speed is not None:
        try:
            ti.playback_speed = float(speed)
        except (TypeError, ValueError):
            ti.playback_speed = None


def parse_track(raw: bytes) -> TrackInfo | None:
    """Парсит трек из payload. Поддерживает push-формат и state-обёртку.

    Два наблюдаемых формата:
    1) Push (flat): {"artists":[...], "trackId":..., "playing":...}
    2) State (info-обёртка): {"artists":[...], "trackId":..., "duration":...},
       при этом "playing"/"position"/"shuffle" — уровнем выше, в player{}.
    """
    s = raw.decode("utf-8", errors="ignore")

    found = _find_track_json(s)
    if found is None:
        return None
    data, start = found

    # state-формат — статусные поля ищем в окружающем player{}
    outer: dict[str, Any] = {}
    if "playing" not in data:
        outer = _find_outer_player(s, start)

    ti = TrackInfo(raw=data)
    ti.title = data.get("title")
    ti.track_id = str(data.get("trackId")) if data.get("trackId") else None
    ti.playlist_title = data.get("playlistTitle") or outer.get("playlistTitle")
    ti.playlist_type = data.get("playlistType") or outer.get("playlistType")
    pid = data.get("playlistId") or outer.get("playlistId")
    ti.playlist_id = str(pid) if pid is not None else None
    ti.media_source = data.get("mediaSource") or outer.get("mediaSource")
    ti.provider = data.get("provider") or outer.get("provider")
    ti.explicit = bool(data.get("explicit", False))
    ti.liked = bool(data.get("like", False))
    for src in (data, outer):
        if "playlistLike" in src:
            ti.playlist_liked = bool(src["playlistLike"])
        if "childMode" in src:
            ti.child_mode = bool(src["childMode"])
        if "playingPending" in src:
            ti.buffering = bool(src["playingPending"])
    if "hasLyrics" in data:
        ti.has_lyrics = bool(data.get("hasLyrics"))

    dur = data.get("duration") or outer.get("duration") or 0
    ti.duration_sec = int(dur) if dur else None

    _parse_artists(ti, data)
    _parse_release(ti, data)
    _parse_position(ti, data, outer)
    _parse_playback_status(ti, data, outer)
    return ti


def parse_queue(raw: bytes) -> list[QueueTrack]:
    """Парсит очередь воспроизведения из ответа op=17.

    Формат: envelope `{5:{17:{5:<JSON-массив>}}}`, где массив —
    `[{"explicit":bool,"trackId":int}, ...]`. Любой сбой → пустой список.
    """
    try:
        decoded = _decode_tlv(raw)
    except Exception:  # pragma: no cover — _decode_tlv защищён, но на всякий
        return []

    body = decoded.get(5)
    if not isinstance(body, dict):
        return []
    inner = body.get(17)
    if not isinstance(inner, dict):
        return []
    arr_str = inner.get(5)
    if not isinstance(arr_str, str):
        return []
    try:
        items = json.loads(arr_str)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(items, list):
        return []

    out: list[QueueTrack] = []
    for it in items:
        if isinstance(it, dict) and it.get("trackId") is not None:
            out.append(
                QueueTrack(
                    track_id=str(it["trackId"]),
                    explicit=bool(it.get("explicit", False)),
                )
            )
    return out


def _parse_bt_devices(
    raw: bytes, op_tag: int, devices_field: int, with_rssi: bool
) -> list[BluetoothDevice]:
    """Общий парсер BT-списков (op=19 спаренные / op=21 найденные).

    Навигация: envelope[5] → [op_tag] → repeated device-сообщения. Каждое —
    {1:mac, 2:name, 3:connected|rssi}. Любой сбой → пустой список.
    """
    try:
        env = _decode_repeated(raw)
    except Exception:  # pragma: no cover
        return []
    body = env.get(5)
    if not body:
        return []
    op_field = _decode_repeated(body[0]).get(op_tag)
    if not op_field:
        return []
    dev_raws = _decode_repeated(op_field[0]).get(devices_field, [])
    out: list[BluetoothDevice] = []
    for dev_raw in dev_raws:
        if not isinstance(dev_raw, bytes):
            continue
        d = _decode_tlv(dev_raw)
        mac = d.get(1)
        if not isinstance(mac, str):
            continue
        name = d.get(2) if isinstance(d.get(2), str) else ""
        dev = BluetoothDevice(mac=mac, name=name)
        if with_rssi:
            rssi = d.get(3)
            dev.rssi = rssi if isinstance(rssi, int) else None
        else:
            dev.connected = bool(d.get(3))
        out.append(dev)
    return out


def parse_paired_bt(raw: bytes) -> list[BluetoothDevice]:
    """op=19 GetPairedBluetoothDevices → список спаренных BT-устройств."""
    return _parse_bt_devices(raw, op_tag=19, devices_field=1, with_rssi=False)


def parse_scanned_bt(raw: bytes) -> list[BluetoothDevice]:
    """op=21 GetScannedBluetoothDevices → список найденных BT-устройств."""
    return _parse_bt_devices(raw, op_tag=21, devices_field=2, with_rssi=True)
