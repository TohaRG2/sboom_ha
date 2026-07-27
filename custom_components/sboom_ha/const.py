"""Константы интеграции sboom_ha."""
from __future__ import annotations

DOMAIN = "sboom_ha"
DEFAULT_NAME = "SberBoom"
DEFAULT_PORT = 20000
DEFAULT_USER_AGENT = "WebSocket++/0.8.2"

# Встроенная панель управления (frontend): раздача www/ + built-in panel.
PANEL_URL_PATH = "sboom"
PANEL_STATIC_PATH = "/sboom_panel"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_NAME = "client_name"
CONF_PIN_ACCESS_TOKEN = "pin_access_token"

# Интервалы
RECONNECT_BACKOFF_SEC  = (1, 2, 5, 10, 30, 60)
PAIR_BUTTON_TIMEOUT_SEC = 120   # сколько ждать нажатия "+" на колонке

# WS ping/pong — транспортный heartbeat: детектит half-open TCP (колонка
# обесточена без FIN/RST), при котором send() успешен, а связи давно нет.
WS_PING_INTERVAL_SEC = 20
WS_PING_TIMEOUT_SEC  = 10

# Сессия считается стабильной, только если прожила столько секунд — иначе
# reconnect-backoff не сбрасывается. Без этого flapping-соединение (connect
# проходит, но сразу рвётся) даёт вечный tight-loop реконнектов раз в 1-2 с,
# при котором порог недоступности никогда не срабатывает.
STABLE_SESSION_SEC = 30

# Столько подряд полностью неудачных poll-циклов при живом (на вид) сокете →
# принудительный reconnect: страховка от half-open, не пойманного WS ping.
POLL_FAILURES_BEFORE_RECONNECT = 2

# Опкоды операций (теги полей в request_data).
OP_PIN_CONNECT       = 4
OP_GET_META_DATA     = 10
OP_GET_STATE         = 12
OP_FIND_REMOTE       = 13  # поиск пульта ДУ
OP_SET_VOLUME        = 14
OP_SET_TRACK_POS     = 15
OP_MEDIA_COMMAND     = 16
OP_GET_PLAYING_QUEUE = 17
# op 18: предположительно keep-alive (по наблюдению за поведением клиента).
# Фаззингом НЕ подтверждён — на пустой/простой запрос молчит (timeout).
# Эксперимент показал: транспортный WS ping/pong держит сессию сам (≥150 c
# idle без op 18 — соединение живо), так что для поддержания связи op 18
# избыточен. Оставлен как дешёвая (fire-and-forget) страховка на случай
# application-level idle-таймаута на длинных паузах, который мы не проверяли.
OP_KEEP_ALIVE        = 18
OP_GET_PAIRED_BT     = 19  # список спаренных Bluetooth-устройств
OP_BT_DEVICE_COMMAND = 20  # команда BT-устройству (connect/disconnect/remove)
OP_GET_SCANNED_BT    = 21  # список найденных Bluetooth-устройств
OP_BT_DISCOVERABLE   = 22  # режим Bluetooth-сопряжения
OP_SET_PLAYBACK_SPEED = 23  # скорость воспроизведения, float-кодировка (research exp_22)

# Скорость воспроизведения: границы и пресеты для select-entity.
# 0.0 — битое состояние колонки (см. research exp_22), поэтому минимум 0.5.
PLAYBACK_SPEED_MIN = 0.5
PLAYBACK_SPEED_MAX = 2.0
PLAYBACK_SPEED_OPTIONS = ["0.5", "0.75", "1.0", "1.25", "1.5", "1.75", "2.0"]

# Опкоды медиа-команд (поле action в media-command-операции).
MEDIA_CMD_MUTE             = 0
MEDIA_CMD_UNMUTE           = 1
MEDIA_CMD_NEXT             = 2
MEDIA_CMD_PREV             = 3
MEDIA_CMD_PLAY             = 4
MEDIA_CMD_PAUSE            = 5
MEDIA_CMD_LIKE             = 6
MEDIA_CMD_REMOVE_LIKE      = 7
MEDIA_CMD_START_MULTIROOM  = 8
MEDIA_CMD_SHUFFLE_ON       = 9
MEDIA_CMD_SHUFFLE_OFF      = 10
MEDIA_CMD_REPEAT_NONE      = 11
MEDIA_CMD_REPEAT_PLAYLIST  = 12
MEDIA_CMD_REPEAT_TRACK     = 13
MEDIA_CMD_DISLIKE          = 14
MEDIA_CMD_REMOVE_DISLIKE   = 15

# Команды BT-устройству (поле cmd в op=20).
BT_CMD_CONNECT    = 0
BT_CMD_DISCONNECT = 1
BT_CMD_REMOVE     = 2

# Тип токена для PIN-сессии (единственный поддерживаемый этой интеграцией).
TOKEN_TYPE_PIN_AUTH = 1

# Поля envelope верхнего уровня WS-кадра (теги TLV).
ENVELOPE_FIELD_MSG_ID = 2        # id запроса — матчинг request/response
ENVELOPE_FIELD_REQUEST_DATA = 5  # request_data — вложенные op-поля

# Статусы pair-handshake (наблюдаемое поведение прошивки).
# Первый ответ (поле 4 в request_data):
PAIR_STATUS_WAITING    = 1  # ждём нажатия «+», sub[2] — id сессии
PAIR_STATUS_AUTHORIZED = 2  # авторизовано сразу, sub[2] — pin-токен
PAIR_STATUS_BUSY       = 3  # pair-сессия уже занята
PAIR_STATUS_DISABLED   = 5  # pair-режим выключен на колонке
# Второй ответ (поле 6, после нажатия «+»):
PAIR_CONFIRM_OK       = 1   # sub[2] — финальный pin-токен
PAIR_CONFIRM_REJECTED = 2

# Единая нормализация repeat-значений → канон {"off","all","one"}.
# API-метаданные отдают none/playlist/all/track/one, HA RepeatMode — off/all/one,
# select-опции — off/playlist/track. Один источник вместо трёх локальных карт.
REPEAT_TO_CANONICAL = {
    "none":     "off",
    "off":      "off",
    "playlist": "all",
    "all":      "all",
    "track":    "one",
    "one":      "one",
}

# mDNS discovery
ZEROCONF_TYPE = "_staros._tcp.local."

# Свойства из mDNS TXT записей (которые публикует колонка)
MDNS_PROP_NAME      = "name"        # "SberBoom Home"
MDNS_PROP_TYPE      = "type"        # "sberboom-r2"
MDNS_PROP_ID        = "id"          # device serial / id
MDNS_PROP_FIRMWARE  = "v"           # firmware version

# Конфиг entry
CONF_DEVICE_ID       = "device_id"
CONF_DEVICE_MODEL    = "device_model"
CONF_DEVICE_NAME     = "device_name"
CONF_DEVICE_FIRMWARE = "device_firmware"

# Public image CDN (без auth). Параметры: type=release|track|artist, id, size=NxN.
ZVUK_IMAGE_CDN = "https://cdn-image.zvuk.com/pic"
COVER_SIZE = "600x600"

# Lyrics: сколько треков держим в LRU-кэше coordinator.
LYRICS_CACHE_MAX = 64

# ─── Опции (entry.options), редактируются через Options Flow ───
OPT_VOLUME_POLL_INTERVAL = "volume_poll_interval"      # секунды, default 15
OPT_AVAILABILITY_THRESHOLD = "availability_threshold"  # подряд неудач, default 3
OPT_KEEPALIVE_INTERVAL = "keepalive_interval"          # секунды, default 25
OPT_LYRICS_ENABLED = "lyrics_enabled"                  # bool, default True
OPT_LYRICS_OFFSET = "lyrics_offset"                    # сек, сдвиг лирики, default 0.0
OPT_LYRICS_NETEASE = "lyrics_netease_fallback"         # bool: NetEase как резерв, default True
OPT_KARAOKE_FILL = "karaoke_fill"                      # bool: посимвольная закраска строки, default False
OPT_PANEL_ENABLED = "panel_enabled"                    # bool: встроенная панель плеера в боковом меню, default True

# Караоке-стрим: интервал кадров при активном воспроизведении (5 FPS —
# достижимо после кэширования blur-фона и шрифтов; JPEG-encode 720p ~10-20 мс).
LYRICS_FRAME_INTERVAL_SEC = 0.2

# Default 15s — track changes приходят push-events через subscribe-stream
# (см. research/PROTOCOL.md); volume/mute добираем поллингом. Пушится ли
# громкость — вопрос открытый (research-доки противоречат друг другу),
# см. DEBUG-лог «state-push получен» в coordinator._handle_event.
DEFAULT_VOLUME_POLL_INTERVAL = 15
DEFAULT_AVAILABILITY_THRESHOLD = 3
DEFAULT_KEEPALIVE_INTERVAL = 25
DEFAULT_LYRICS_ENABLED = True
DEFAULT_LYRICS_OFFSET = 0.0
DEFAULT_LYRICS_NETEASE = True
DEFAULT_KARAOKE_FILL = False  # по умолчанию статичная строка (закраска — опционально)
DEFAULT_PANEL_ENABLED = True  # панель плеера включена по умолчанию
