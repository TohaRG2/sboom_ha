# Живая схема состояния колонки (реальные имена полей)

Снято с колонки (`GET_STATE` op12 / `GET_META_DATA` op10 / `GET_PLAYING_QUEUE` op17).
Только имена+типы (значения приватны, не коммитятся; см. `capture_state.py`).

**Snapshot source:** SberBoom Home (product `sberboom-r2` по DeviceInfo).
При смене крупной версии серверного стека имеет смысл пересобрать дамп
(`python research/capture_state.py`) — периодически появляются новые ключи
в GET_STATE-подсистемах (equalizer/room-correction/multi-room и т.п.).

**Важно — два разных представления протокола (НЕ путать):**
- **Бинарные proto-сообщения** (`staros.proto`) — wire-команды, что HA *шлёт*.
  Поля snake_case, с номерами (`channel`, `mr_session_id`).
- **JSON состояние/metadata** (этот файл) — отдельная JSON-сериализация, что HA
  *читает*. Ключи преимущественно camelCase (`channelFromConfig`, `trackId`).
  Транспортируется как СТРОКА внутри proto-обёртки (`Metadata.contents = 1`),
  а не как proto-поля.

Пересечение имён между слоями почти всегда пустое (разный case + разные namespace),
но не строго нулевое: единичные общие идентификаторы встречаются (`character`,
`provider`, `title`, `contents` совпадают лексически). Ниже — схема JSON-состояния
для чтения в HA, а НЕ имена полей бинарных proto-команд.

## GET_STATE (op12) — top-level (82 dot-path'а под `[0]`-веткой)

Число 82 — это `len(all_keys.json["state"])` из `capture_state.py`: рекурсивный
tally только по индексу `[0]` в списках. Настоящий контракт GET_STATE шире —
внутри `background_apps.[].state.player.*` живут ещё поля now-playing (см. подраздел
ниже). Их не видно в этом верхнеуровневом tally, но парсит `_parsers.py`.

```
alarm: dict
alarm.alarms: list
alarm.alarmsCounter: int
alarm.clocks: list
alarm.playing: NoneType
alarm.status: int
alarm.timers: list
assistant: dict
assistant.auto_volume: bool
assistant.character: str
background_apps: list
background_apps.[].app_info: dict
background_apps.[].app_info.frontendType: str
background_apps.[].app_info.systemName: str
background_apps.[].state: dict
capabilities_state: dict
capabilities_state.led_display: dict
capabilities_state.led_display.brightness: int
capabilities_state.led_display.turned_on: bool
current_app: dict
current_app.app_info: dict
current_app.state: dict
deviceGroups: dict
deviceGroups.soundBar: NoneType
deviceSelector: dict
deviceSelector.castGroup: list
deviceSelector.dsGroup: list
deviceSelector.enabled: bool
deviceSelector.features: list
deviceSelector.locked: bool
deviceSelector.qcGroup: list
deviceSelector.roomGroup: list
deviceSleep: dict
deviceSleep.systemState: str
device_segments: list
homeSecurity: dict
homeSecurity.enabled: bool
locale: dict
locale.locale: str
location: dict
location.accuracy: float
location.lat: float
location.lon: float
location.source: str
location.timestamp: int
morning_show: dict
morning_show.from_show: bool
morning_show.in_show: bool
multiroom: dict
multiroom.enabled: bool
multiroom.mode: str
multiroom.stereoPair: dict
multiroom.stereoPair.active: bool
multiroom.stereoPair.channelFromConfig: str
multiroom.stereoPair.pairDeviceFromConfig: str
network: dict
network.connection_type: str
network.ip: str
network.updated_timestamp_ms: int
proactivityNotification: dict
proactivityNotification.hasNotification: bool
reminders: dict
reminders.reminders: dict
reminders.reminders.time_reminders: dict
sbercast: dict
sbercast.devices: list
sbercast.enabled: bool
subscrDeviceInfo: dict
subscrDeviceInfo.isSubscrDevice: bool
time: dict
time.timestamp: int
time.timezone_id: str
time.timezone_offset_sec: int
timesync: dict
timesync.unixtime: float
user_settings: dict
user_settings.age_mode: str
user_settings.enable_child_voice_explicit: bool
user_settings.multi_profile: bool
volume: dict
volume.muted: bool
volume.percent: int
```

### GET_STATE — `background_apps.[].state.player.*` (радио / Bluetooth fallback)

Когда `GET_META_DATA` (op10) пуст (обычно на радио и Bluetooth), парсер
`_parsers.py:track_from_state` берёт метаданные из этой ветки. Верхнеуровневый
tally не спускается сюда, но поля активно используются в HA.

```
# .state.player
background_apps.[].state.player.playing: bool
background_apps.[].state.player.position: int         # секунды (int в state-формате; в metadata — float в поле position)
background_apps.[].state.player.duration: int
background_apps.[].state.player.mode: str             # "radio" | "music" | "podcast" | "wave" | "bluetooth"
background_apps.[].state.player.shuffle: bool
background_apps.[].state.player.repeatType: str
background_apps.[].state.player.playbackSpeedRate: float
background_apps.[].state.player.stateChangedTimestamp: int   # ms epoch (см. metadata тоже)
background_apps.[].state.player.type: str             # "audio" | "show"
background_apps.[].state.player.live: bool
background_apps.[].state.player.flac_enabled: bool
background_apps.[].state.player.endlessCached: bool
background_apps.[].state.player.childMode: bool
background_apps.[].state.player.enableChildVoiceExplicit: bool
background_apps.[].state.player.multiProfile: bool
background_apps.[].state.player.profileType: str

# .state.player.info  — now-playing блок
background_apps.[].state.player.info.title: str
background_apps.[].state.player.info.trackId: str
background_apps.[].state.player.info.artists: list           # [{id, name}]
background_apps.[].state.player.info.releases: list          # [{id, name}]
background_apps.[].state.player.info.playlistId: str
background_apps.[].state.player.info.playlistTitle: str
background_apps.[].state.player.info.playlistType: str
background_apps.[].state.player.info.provider: str           # "zvuk" | "radio" | "bluetooth" | ...
background_apps.[].state.player.info.mediaSource: str
background_apps.[].state.player.info.hasLyrics: bool         # → binary_sensor.track_has_lyrics
background_apps.[].state.player.info.like: bool
background_apps.[].state.player.info.playlistLike: bool
background_apps.[].state.player.info.explicit: bool
background_apps.[].state.player.info.stationName: str        # для радио
background_apps.[].state.player.info.tags: list
background_apps.[].state.player.info.total: int              # длина очереди
background_apps.[].state.player.info.collectionState: str
background_apps.[].state.player.info.playlistCollectionState: str
```

## GET_META_DATA (op10) — Metadata (23 полей)

```
artists: list
artists.[].id: str
artists.[].name: str
childMode: bool
explicit: bool
like: bool
mediaSource: str
playbackSpeedRate: float
playing: bool
playingPending: bool
playlistId: str
playlistLike: bool
playlistTitle: str
playlistType: str
provider: str
releases: list
releases.[].id: str
releases.[].name: str
repeatType: str
shuffle: bool
stateChangedTimestamp: int  # ms since epoch — используется как fallback для position_ts_ms в state-формате (_parsers.py:427)
title: str
trackId: str
```

## GET_PLAYING_QUEUE (op17) (2 полей)

```
explicit: bool
trackId: int
```

## Ключевое для HA

- `capabilities_state.led_display.{brightness,turned_on}` — реальные возможности (экран/подсветка).
- `multiroom.{enabled,mode,stereoPair.{active,channelFromConfig,pairDeviceFromConfig}}` — мультирум/стереопара.
- `deviceSelector.{castGroup,dsGroup,qcGroup,roomGroup,features,locked}` — группы устройств.
- `alarm.{alarms,clocks,timers,status,alarmsCounter}` — будильники/таймеры (`alarm.status`, `alarm.clocks` тоже полезны для диагностики).
- `user_settings.{age_mode,enable_child_voice_explicit,multi_profile}` — профиль/родительский контроль.
- `network.{connection_type,ip,updated_timestamp_ms}`, `location.{lat,lon,accuracy,source}`, `volume.{muted,percent}`.
- `background_apps.[].state.player.info.hasLyrics` — метка «у трека есть текст на стороне Sber», читается `_parsers.py:492`, пробрасывается в `binary_sensor.track_has_lyrics`.
- `background_apps.[].state.player.{position,duration,playing,mode}` — now-playing для радио/BT (когда op10 пуст).
