# Протокол исследования (живая сессия)

Этот файл — **research log**: фактическая последовательность команд и наблюдений при black-box discovery proprietary WebSocket-протокола неизвестного устройства. Все команды воспроизводимы, ничего не основано на инсайдерском знании реализации — только на реакциях устройства.

## Целевой стек (что узнали об endpoint)

```
TLS 1.3 (self-signed cert, 922 bytes DER)
  └─ WebSocket++/0.8.2 (server header)
       └─ binary frames
            └─ Google proto-wire encoding (varint + length-delimited)
                 └─ JSON в length-delimited полях для метаданных
```

## Hop-by-hop: что делал, что увидел

### 1. Network discovery — mDNS не сработал, TCP-scan нашёл

```bash
# mDNS: только 4 service-types в подсети, устройства среди них нет
python research/01_discover.py --debug
# → Wi-Fi AP блокирует client-to-client multicast (Wi-Fi isolation)

# Системные утилиты подтвердили:
nmap -p 20000,8080,8443 --open 192.168.1.0/24
# → Nmap scan report for SberBoom-Home-3349.lan (192.168.1.61)
#    20000/tcp open

avahi-resolve -n SberBoom-Home-3349.local
# → timeout (mDNS не доходит)
ping -c 1 -W 1 224.0.0.251
# → ответил только iPhone, не наше устройство
```

**Вывод**: на этой Wi-Fi-сети mDNS-discovery от устройства не работает. TCP-scan надёжнее. Auto-fallback в `01_discover.py` сейчас именно это и делает.

### 2. Endpoint probe — TLS WS подтверждён

```bash
python research/02_probe.py --host 192.168.1.61 --port 20000
# → TLS 1.3, AES-256-GCM, self-signed cert (922b)
# → HTTP GET / → 400 Bad Request, Server: WebSocket++/0.8.2
# → WebSocket Upgrade → OK, Status 101
# Endpoint = TLS WebSocket. Move to capture (03).
```

### 3. Initial passive capture — устройство молчит

```bash
python research/03_capture.py --host 192.168.1.61 --port 20000 --duration 60
# → 0 messages. Сервер слушает, но не отвечает на минимальные пробы и не пушит сам.
```

**Вывод**: устройство ждёт **полной валидной обёртки** прежде чем отвечать. Single-byte probe и passive listen не помогают.

### 4. Envelope structure — multi-field hypothesis testing

`04_fuzz_envelope.py` single-field probes тоже timeout. Нужны **multi-field combos**.

Гипотеза: типичный RPC-envelope = `{type: REQUEST, request_id, body}`. Это **публичная конвенция** (gRPC, Google-RPC), не вендор-знание.

```python
# Минимальная обёртка, которая получает reply:
parts = [
    field(1, 0, 2),       # type-varint = 2 (REQUEST)
    field(2, 2, rid_uuid),# request_id = uuid string
    field(5, 2, b""),     # body = empty (или nested(op, ...))
]
```

Reply: `{1:2, 2:'<наш-rid>', 3:5, 5:''}`

**Что узнали из reply**:
- field 2 echo'нул наш rid → **rid_tag = 2** ✓
- field 1 = 2 → response type echo
- field 3 = error/status code (5 = недостаточно полей; 4 = другая ошибка)
- field 5 = body (пустой при ошибке)

Полная обёртка с 8 полями (поля 1, 2, 3, 5, 6, 7, 10, 11) — status code меняется с 5 на 4 → значит сервер дополнительно валидирует другие поля.

### 5. Op-tag sweep — нашли pair-init без auth

```python
# Шлём envelope с body=field(op, 2, field(1, 2, b"")) для op=1..24
# (op=N — это field-tag в *body*, означает «какую операцию вызвать»)
```

Результат: **большинство ops возвращают `status=4` (auth-required)**, но **op=4, 5, 6 ведут себя по-другому**:

```
op=4 → 84b reply, body содержит {4: {1:1, 2:'<session-uuid>'}}
op=5 → 44b с body={5: ...}
op=6 → 44b с body={6: ...}
```

Интерпретация:
- op=4 = **pair-init** (returns session_id, переводит устройство в режим ожидания)
- op=5 = вероятно cancel (озвучилось «отключение отменено» при предыдущем sweep'e)
- op=6 = pair-confirm (как server → client push после нажатия кнопки)

### 6. Pair handshake — получили токен

```python
# Послали op=4 init, держали WS открытым
# Оператор услышал озвучку «нажмите плюс» и нажал кнопку

# Reply #1 (sync): {1:2, 2:rid, 5: {4: {1:1, 2:'<session-id>'}}}
# Reply #2 (push после кнопки): {1:2, 2:'<новый-rid>', 5: {6: {1:1, 2:'<TOKEN>'}}}
```

**Получен токен**: UUID-формат, выдан в sub-field 2 внутри response op=6.

### 7. Authorized op-sweep — нашли GET_STATE / GET_METADATA / MEDIA_COMMAND

Repeat sweep, но с токеном в `field(3, 2, token)`:

| op | size | тип |
|----|------|------|
| 4 | 84b | pair-init (re-arms) |
| **10** | **899b** | **GET_METADATA** — current track JSON |
| **12** | **3617b** | **GET_STATE** — full state JSON |
| 16 | 45b | ack — кандидат на MEDIA_COMMAND |
| 17 | 287b | средний JSON (queue/search?) |
| 1, 2, 8, 24 | 44-46b | success ack-ops |
| остальные | 44-46b | возвращают status=? |

GET_METADATA payload:

```json
{
  "artists": [{"id": "703663", "name": "Whitesnake"}],
  "title": "Is This Love",
  "duration": 284,
  "playing": true,
  "position": {"tsMs": ..., "val": 8},
  "playlistTitle": "Персональная волна",
  "provider": "zvuk",
  "trackId": "66740880",
  "shuffle": false, "repeatType": "none",
  "explicit": false, "like": false
}
```

Все имена полей **видны в payload** — это и есть единственный self-describing слой протокола.

### 8. Action-fuzz через diff — полная таблица команд

Кандидат на MEDIA_COMMAND_OP — те что дают ack 44-46b. Полный sweep `op=16, action=0..15` с diff GET_STATE+GET_METADATA:

| action | семантика | как нащупали |
|--------|-----------|--------------|
| 0 | **MUTE** | volume.muted: false→true |
| 1 | **UNMUTE** | volume.muted: true→false |
| 2 | **NEXT** | trackId изменился вперёд |
| 3 | **PREV** | trackId вернулся назад |
| 4 | **PLAY** | (из paused state) playing: false→true |
| 5 | **PAUSE** | playing: true→false |
| 6 | **LIKE** | like: false→true |
| 7 | **REMOVE_LIKE** | like: true→false |
| 8 | ? (no observable diff) | вероятно DISLIKE или START_MULTIROOM — не виден в стандартном state |
| 9 | **SHUFFLE_ON** | shuffle: false→true |
| 10 | **SHUFFLE_OFF** | shuffle: true→false |
| 11 | **REPEAT_NONE** | (из playlist) repeatType: playlist→none |
| 12 | **REPEAT_PLAYLIST** | repeatType: none→playlist |
| 13 | **REPEAT_TRACK** | repeatType: playlist→track |
| 14 | ? | repeatType: track→none + side-effect track-jump |
| 15 | ? (no observable diff) | вероятно REMOVE_DISLIKE |

**Ключевые приёмы fuzz'а**:
- baseline-snapshot **до** action и **после** — diff даёт label
- При no-diff менять initial state (paused → play, repeat=playlist → repeat=none) и пробовать снова
- Иногда single-action даёт side-effects (action=14 затронул и repeatType, и trackId — flag которое стоит расследовать дополнительно)
- Поля `dislike`, `multiroom_state` могут не присутствовать в стандартном GET_STATE/GET_METADATA — нужны дополнительные state-источники

## Финальная карта (этот device)

```
endpoint:        wss://192.168.1.61:20000/
auth-token:      <получен через pair-flow с физическим нажатием кнопки>

envelope:        type=field(1,0,2)
                 rid=field(2,2,uuid)
                 token=field(3,2,<token>)
                 body=field(5,2,nested-op)
                 (+опц: 6=token-type=1, 7=client_name, 10=is_request=1, 11=client_id)

ops (in body):   4  = pair-init (server returns session via op-response field 4)
                 10 = GET_METADATA → JSON {trackId, title, artists, position, ...}
                 12 = GET_STATE → большой JSON со всем
                 16 = MEDIA_COMMAND (принимает inner field(1,0,action))
                 17 = ? (returns 287b JSON)

media-actions    0=mute, 1=unmute, 2=next, 3=prev, 4=play, 5=pause,
(in op=16):      6=like, 7=remove_like, 8=?(dislike?),
                 9=shuffle_on, 10=shuffle_off,
                 11=repeat_none, 12=repeat_playlist, 13=repeat_track,
                 14=?(repeat_none + jump), 15=?(remove_dislike?)

response status  field 3 в response:
codes:             1 = ok / waiting
                   4 = error (auth-required / invalid op)
                   5 = error (missing fields)
```

## Полная последовательность для повтора

```bash
# 1. Discovery (если mDNS не работает — auto-fallback на TCP-scan)
python research/01_discover.py --debug

# 2. Probe найденного endpoint
python research/02_probe.py --host <IP> --port 20000

# 3. Auto-pipeline: discovery + probe + envelope inference + pair + op-sweep + action-fuzz
python research/auto_discover.py --host <IP> --port 20000
# → интерактив: будет просить нажать pair-кнопку и затем запустить трек

# 4. Опционально: расширенный фуззинг сверху найденной карты
python research/07_deep_fuzz.py --map protocol_map.json
```

## Расширенные исследования (после base sweep'а)

### Sub-field fuzz op=10/12

Перебор inner-параметров `field(1|2|3, 0|2, varied)` для GET_METADATA и GET_STATE:

| input | op=10 | op=12 |
|-------|-------|-------|
| empty | 882b | 3603b |
| varint(0/1/10) на любом subfield | 882b same | ~3603b same |
| string на subfield 1 или 2 | **timeout** | **timeout** |
| bytes на subfield 1 | **timeout** | **timeout** |

**Вывод**: обе операции **не параметризуются** простыми varint/string/bytes. String- и bytes-input → сервер получает но не отвечает (вероятно ожидает специфическую schema-структуру для filter / search).

### Pair-flow братья op=4 / op=5 / op=6

| op | inner | response | смысл |
|----|-------|----------|-------|
| 4  | `field(1,2,"")` empty | `{4:{1:1, 2:session_id}}` | **pair-init** (status=1=awaiting button) |
| 4  | любое не-empty | `{4:{1:4}}` | error: pair-init ждёт строго empty |
| 5  | любое | `{5:''}` ack | **pair-cancel** (озвучивание «отключение отменено») |
| 6  | любое | `{6:''}` ack | acknowledge / state-check |

**Структурный insight**: response body содержит **echo op-tag из request**, внутри — `field(1, 0, status)` + опц. `field(2, 2, payload)`. Wrapping симметричен:

```
request:  body = field(op, 2, ...)
response: body = field(op, 2, {1: status, 2: data?})
```

### Push-subscribe trigger найден

`op=10` (GET_METADATA) **активирует subscribe-stream**: после однократного запроса колонка push'ит unsolicited update'ы на любое media-event. Размер push'ей (~1740b) больше sync reply (~880b) — расширенная метадата.

Покрываются track/play/pause/volume changes. Mute не всегда триггерит (зависит от текущего state).

```python
# subscribe (1 раз на сессию):
ws.send(envelope(body=field(10, 2, field(1, 2, b""))))
# затем все async-сообщения от ws.recv() — push-events
```

### Dark actions 8, 14, 15 — explored

| action | observed | hypothesis |
|--------|----------|------------|
| 8 | `background_apps` **переупорядочены** (voice_auth поднимается в top) | **focus voice_auth** или trigger voice-assistant |
| 14 | track-jump (как next), background_apps tail rearranged | **next-batch / new queue source** |
| 15 | background_apps переупорядочены, track не меняется | focus-related toggle |

**Открытие**: `state.background_apps` — это **z-order стек активных приложений** на устройстве (music, morning_show, bluetooth_media_control, voice_auth, pager, geo_fixer_app). Action 8/15 двигают apps в этом стеке.

## Network discovery — Wi-Fi isolation как типичный блокер

На нашей сети `mDNS broad-browse` нашёл всего 4 service-type'а (HA, matter, workstation), а целевое устройство **не попало в список** хотя реально присутствует (192.168.1.61, ARP-таблица подтвердила). Системные утилиты:

```
ping 224.0.0.251       → отвечает только iPhone, не наше устройство
avahi-resolve -n       → timeout (mDNS-multicast не доходит)
nmap -p 20000 /24      → ✓ найден (через TCP/ARP)
```

**Hypothesis-конфирм**: Wi-Fi AP блокирует **client-to-client** broadcast/multicast, но **не** Wi-Fi→Ethernet. Hosting HA в QEMU-VM на Ethernet-машине → multicast от Wi-Fi-устройства до HA проходит через AP-uplink. От Wi-Fi-устройства до Wi-Fi-клиента (наш ноутбук) — режется client-isolation.

Вывод: TCP-port-scan **обязателен** как fallback в любом discovery-пайплайне для подобных сетей.

## Subsystems в GET_STATE — карта дальнейшего исследования

Top-level keys в GET_STATE (op=12) — это **семейства подсистем устройства**, каждое потенциально имеет свой набор управляющих ops:

```
alarm                 — будильники
assistant             — voice-assistant настройки (auto_volume, ...)
background_apps       — z-order стек активных apps (см. ниже)
capabilities_state    — { led_display: { brightness, turned_on } }
current_app           — текущий focused app
deviceGroups          — группы устройств (для multiroom?)
deviceSelector        — выбор активного устройства
deviceSleep           — состояние сна
device_segments       —
homeSecurity          — security-настройки
locale                — язык / регион
location              —
morning_show          — «утреннее шоу» feature
multiroom             — multi-room sync
network               — сетевые настройки
proactivityNotification —
reminders             — напоминания
sbercast              — встроенный media-cast app
subscrDeviceInfo      — info о подписке
time / timesync       — синхронизация времени
user_settings         —
volume                — { muted, percent }
```

Каждая subsystem — **потенциальное направление**: должны быть ops для управления (set-volume, set-led-brightness, set-alarm, join-multiroom, ...). Базовый sweep 1..24 нашёл только media-ops; диапазон 25..63 + другие body-tag'и могут раскрыть остальные.

## background_apps — z-order стек активных приложений

`state.background_apps` — массив активных system-app'ов с их состояниями. На нашем устройстве:

| systemName | примечание |
|------------|------------|
| music | основной music-плеер (provider=zvuk) — **наш текущий target** |
| sbercast | media-cast для внешних источников |
| morning_show | «утреннее шоу» |
| bluetooth_media_control | BT-management |
| voice_auth | voice-authentication / биометрия |
| pager | системный pager |
| geo_fixer_app | геолокация |

**Открытие**: dark-actions 8/15 на op=16 двигают app'ы в этом стеке. Каждое app может иметь свой own player с своим state. То есть **op=10 (GET_METADATA) даёт state ТОЛЬКО для focused app**. Чтобы получить metadata для других apps — нужен другой op или explicit selection.

## JSON-артефакты для атрибуции

В payload'ах встречаются специфические уникальные имена которые могут служить fingerprint'ом для определения вендора при поиске:

- `spaceshipLaunchUUID` — в GET_METADATA, появляется на каждый запрос (random UUID, не attached к device-id)
- `provider: "zvuk"` — music-streaming source
- `playlistType: "endless"` — endless-recommendations
- `mediaSource: "MUSIC"`
- `frontendEndpoint: "ru.sberdevices.bluetooth_media_control"` / `"ru.sberdevices.music"` — **прямой attribution string** в background_apps

Эти строки — единственный self-describing layer, выдающий контекст. Если сравнивать с trafficом другого вендора — наличие/отсутствие этих имён сразу говорит «это не он».

## Метод multi-field envelope inference (переиспользуемый)

Когда single-field probes молчат — server slim-validation отбрасывает minimal сообщения. Стратегия которая работает:

1. **Hypothesis 1 (минимальный)**: только rid-marker → `field(N, 2, RANDOM_UUID_HEX)` для N=1..K. Echo маркера в reply → нашли rid_tag.
2. **Если silence на single-field** — пробуем **3-field permutation**: `(type_tag, rid_tag, body_tag)` ∈ candidates³ с минимальными values:
   ```python
   [field(t, 0, 2), field(r, 2, uuid), field(b, 2, b"")]
   ```
   Echo маркера + non-empty reply → нащупали все три tag-роли за один матч.
3. **type-varint тестировать на 1, 2, 3** — типичные RPC-codes (REQUEST/RESPONSE/EVENT). Часто =2 для REQUEST.
4. **Symmetrical response wrapping**: response.body содержит **echo op-tag** из request — это позволяет сопоставлять request и response как пары.

Это работает на любом silent-сервере с RPC-семантикой, не специфика устройства.

## Дополнительные находки (sequel session)

### op=14 = SET_VOLUME ✓

Принимает `field(1, 0, percent)` — varint от 0 до max. Чисто 5/5 матчей:
```
op=14 v=15 → percent=15
op=14 v=35 → percent=35
op=14 v=55 → percent=55
op=14 v=75 → percent=75
op=14 v=80 → percent=75 (capped)
op=14 nested → ignored (volume не меняется)
```

**Max-volume cap = 75** — software-limit на устройстве (либо user_setting либо safety).

### op=15 = SEEK_TO_POSITION ✓

Принимает `field(1, 0, seconds)`:
```
op=15 v=60 → position.val=60
op=15 v=120 → position=120
op=15 v=200 → position=200
op=15 v=10000 → past-duration → автоматический NEXT track
```

Если seek превышает длительность — устройство переходит к следующему треку.

### Странные ops в диапазоне 11..23

| op | reply body | observation |
|----|-----------|-------------|
| 8  | str-input → reply size = base+len(s); varint → base+2 | **echo/diagnostic**! Размер ответа линейно отражает размер вложенного payload (см. deep-fuzz session) |
| 9  | varint-input → reply size = base+2 | **echo/diagnostic** для varint-полей |
| 11 | str-input → reply size = base+len(s); прежде наблюдалось `{11: {1: 'foo'}}` | **echo/diagnostic** — третий echo-op в группе |
| 18 | timeout на любой inner | Требует specific format, который мы не угадали |
| 19 | `{19: ''}` ack; str-input → timeout | Стандартный ack |
| 20 | `{20: {1: 1}}` константа | State-flag-getter? Возможно `is_active=true` |
| 21 | `{21: {1: 1}}` константа | То же что op=20 — другой flag |
| 22 | `{22: ''}` ack | Стандартный ack |
| 23 | `{15: ''}` — echo `15` вместо `23`! | **Alias на op=15 (SEEK)?** Или router-mapping |

ops 24-40 (за исключением peculiar) → 44b status=1 ack без body — вероятно **non-existent ops** возвращающие default-ack.

## Открытые вопросы (hypothesis backlog)

Список того что **не нащупано** + что пробовали — для будущих run'ов:

1. ~~**Volume control**~~ ✓ найдено: op=14 (SET_VOLUME) с inner field(1,0,percent), max=75
2. ~~**Seek to position**~~ ✓ найдено: op=15 (SEEK_TO_POSITION) с inner field(1,0,seconds)
3. **Play track by id** — пробовали (exp_16): op=18+varint, op=10/12+nested-string,
   op=17+index, op=16+a=2+sub2=trackId. Ни один не работает. Гипотезы:
   - Требуется schema-payload (nested message со специфической структурой)
   - Возможно через app-specific channel (sbercast, music app внутри background_apps)
   - Может быть **нет** — управление трэками только через NEXT/PREV в queue
4. **Action 8/9 семантика** — observable diff в background_apps есть, но diff
   зашумлён natural recommendation-reorder. Нужен statistical method:
   повторить N раз → найти invariant pattern.
5. **LED brightness/turn-off** — `capabilities_state.led_display.{brightness, turned_on}`
   явно managed, ops неизвестны. Стратегия: sweep с varied inner, diff led_display.
6. **Multi-room** — целая subsystem `multiroom`, её ops неизвестны (action 8 переключал voice_auth, не multiroom).
7. **Alarm management** — set-alarm, list-alarms, delete-alarm.
8. **Subsystem-targeted state queries** — может быть op принимающий `field(1, 2, "alarm")` или `field(1, 0, subsystem_id)` для filtering GET_STATE.
9. **Sound-mode / equalizer** — если такая фича есть.
10. **Bluetooth pair management** — отдельная подсистема.
11. **Device-info / capabilities query** — какую модель устройства, версию firmware, supported-features можно опросить?
12. **Op=18 specific format** — таймаутит на любой простой inner; возможно ждёт schema-message.
13. **Op=20, 21 что за константа `{1: 1}`** — state-flag-getter? Что они показывают?

Для каждого — отдельная экспериментальная сессия. Метод тот же: brute-force op-range + diff state, но с фокусом на конкретные subsystems из GET_STATE.

## Sequel-2 session (negative results)

После закрытия 2 задач (volume/seek), попытались закрыть остальные:

### Action 8/9 statistical analysis (exp_18) → отрицательный

10 повторов для каждого action=8/9 vs control (без action). Diff'ы в
`background_apps` происходят с **тем же** уровнем frequency что в control.

**Вывод**: реорганизация background_apps происходит сама собой
(recommendation engine), независимо от наших команд. Если action=8 что-то
делает — это **не отражается в JSON GET_STATE**, может быть только physical
(audio cue, LED).

### LED control op (exp_19) → отрицательный

Sweep ops 1..40 с `field(1, 0, brightness)`, `field(1, 0, 0/1)` и
`{"brightness":50}` JSON-string в nested. **Ни одна комбинация** не изменила
`capabilities_state.led_display.brightness` или `.turned_on`.

### Расширенный sweep ops 41..80 (exp_20) → все default-ack

**Все** ops 41..80 возвращают одинаковый default-ack (44b status=1 empty body).
Сервер игнорирует unknown ops без ошибки.

**Вывод**: вселенная ops для этого устройства = **только 1..23**. Ops для
LED, alarm, multi-room — не существуют в этом диапазоне.

### op=16 с subfield 2..7 (exp_21) → отрицательный

Sweep alternative subfield-tags для inner-payload. Ничего не меняет в
volume/led/alarm/current_app. Combos (subf1=action + subf2=value) тоже
не дают эффекта.

### Финальная гипотеза

LED, alarm, multi-room, current_app — **read-only** через LAN-API. Контроль
этих subsystems вероятно только через cloud (Sber-серверы) или через
голосового ассистента. LAN-API экспортирует только media-control часть
функционала устройства.

Это значит интеграция HA может корректно **читать** эти поля (через
GET_STATE), но **писать в них нельзя** через WS-протокол.

## Sequel-3 session: deep-fuzz (07_deep_fuzz)

Запуск консолидированного fuzz-инструмента (`07_deep_fuzz.py`) с использованием
готовых артефактов из `protocol_map.json` (host, port, envelope-roles,
auth-token-tag, op-numbers). Цель: ещё раз пройти op-sweep с расширенным
subfield-fuzz, без интерактивных частей.

### Фикс envelope в `make_envelope`

Сначала ВСЕ ответы возвращали 44-байтовый default-ack (status=1, body
пустой) — характеристика **отбрасывания пакета** сервером. Стандартный
single-field конверт (только rid+body+client_id+token) не валиден для
сервера.

После двух итераций исправлений минимальный валидный конверт оказался:

```
field(1, varint, 2)         # type = 2 (REQUEST)
field(rid_tag, varint, rid)
field(body_tag, length, body)
field(client_id_tag, length, "research-client")
field(token_type_tag, varint, 1)
field(token_tag, length, pin_token)
field(7, length, "research")  # client_name
field(10, varint, 1)          # is_request flag
```

Поля 7 и 10 (`client_name` и `is_request`) — обязательны для сервера;
без них пакет молча игнорируется. Это новые знания о структуре конверта,
ранее не задокументированные.

### Подтверждение echo-семантики op=8/9/11

Subfield-fuzz по op=8/9/11 дал чёткий signature **echo overhead**:

```
op=8  base = 46b
  inner s='test'  → reply 52b (+6 = +len('test')+2 framing)
  inner s='long'  → reply 112b (+66 = +60 chars + framing)
op=9  base = 56b
  inner v=varint  → reply +2b (varint encoding overhead)
op=11 base = 44b
  inner s='long'  → reply 110b (+66)
```

**Вывод**: op=8, 9, 11 — это группа **диагностических echo-ops**. Сервер
принимает payload, оборачивает его обратно и возвращает размер,
пропорциональный длине входного string или varint. Семантически эти
ops для нас бесполезны, но полезны для debug: проверка живости/auth/
структуры конверта.

### Push-subscribe пассивный re-listen

В составе deep-fuzz запущена пассивная фаза subscribe-detection (listen
N секунд после каждого op). Все ops дали 0 push-frames в пассивном режиме —
ожидаемо, поскольку push-subscribe (op=10) требует **активного провоцирования**
state-changes (см. exp_10/11), а deep-fuzz не provoке состояние внешним
способом.

### Артефакты сессии

- `research/protocol_map_extended.json` — output deep-fuzz с расширенными op-sweep
- `research/events.jsonl` — chronological event-log

Оба файла большие (~474KB и ~388KB) и регенерируемы из скрипта — поэтому
оставлены в `.gitignore`. Воспроизвести можно командой:
`python research/07_deep_fuzz.py --max-op 24 --action-extra 32`.

## Ключевые уроки

1. **Single-field эвристики не работают** для silent-серверов. Нужны multi-field hypothesis combos с минимально-разумной полной обёрткой (3+ полей).
2. **Type-varint** (`field(1,0,2)`) — общая RPC-конвенция (REQUEST=2 типично для gRPC-like).
3. **Echo request_id** — золотая жила для определения `rid_tag` без подсказок.
4. **diff-based action labelling** — ground truth `op×action → семантика`. Без play-state это бы не работало (наш базовый трек был играющий с самого начала — повезло).
5. **Pair-flow** — двухшаговый: sync-init → physical button → async-push с токеном. Колонка озвучивает "нажмите +", что подтверждает что мы попали в правильный op.
6. **Конверт требует ≥7 полей** — минимум: type, rid, body, client_id, token_type, token, client_name, is_request. Без `client_name (7)` и `is_request (10)` сервер молча отбрасывает пакет (default-ack 44b). Эти поля обнаружены только при отладке `07_deep_fuzz`.
7. **Echo-ops (op=8, 9, 11)** — диагностические; не несут семантики, но удобны для проверки живости/корректности конверта по предсказуемому overhead.

## Разведка `lc` — локальный движок автоматизации (:4242, fw 26.1.7)

Debug-CLI :4242 раздел `lc` = **Local automation debug commands**. Обойдено
`research/lc_explore.py`. Дерево команд:

```
lc
├── db      "Manage database: lc db <cmd>"
│   └── print       → "AutomationController DB dump complete"
│                      (сам дамп уходит в лог устройства, в TCP-сокет — только строка завершения)
└── script  "Manage scripts {reload}"
    └── print       → "Total 0 scripts"
```

**Вывод (важно для оценки control-gap):**

1. В прошивке **есть локальный движок сценариев** — `AutomationController`
   с хранилищем скриптов и БД. Т.е. колонка умеет исполнять автоматизации
   локально, без облака.
2. **Хранилище пустое** — `Total 0 scripts`. На нашей колонке ни одного
   локального сценария не установлено.
3. Debug-CLI даёт **только read-only** доступ: `print` (дамп) и `reload`
   (перечитать хранилище). **Команды установки/инъекции скрипта в :4242 нет** —
   сценарии, судя по всему, провижнятся из облака Sber и складываются в
   локальное хранилище, откуда движок их подхватывает по `reload`.

**Значит для HA-интеграции здесь тупик**: локальный automation-engine
существует, но (а) пуст и (б) не имеет локального пути записи через
открытые интерфейсы. Использовать его как «локальный сценарный движок под
управлением HA» нельзя без облачного канала провижининга. Документируем как
закрытое направление — на будущее сторожит `research/fw_recon.py` (если
обновление прошивки добавит write-команду в `lc`, diff это покажет).

## Порт :33000 — молчаливый one-shot ingest (2026-07-11, не вскрыт)

Странный порт, замеченный пользователем: **закрывается после ЛЮБОГО connect
(даже неуспешного), оживает только после ребута**. Открывается ~20 c после
того, как поднимается :4242 (при margin 15 c — connect timeout/SYN-drop, при
20–25 c — connect success). Разведан `research/probe_oneshot.py` (ребут через
`:4242 sys reboot` → ждём :4242 → margin → ЕДИНСТВЕННЫЙ выстрел; 33000 нельзя
поллить — сжигается).

Результаты выстрелов (каждый = 1 ребут):
- **active-пробы** (LF/CRLF/help/?/version/HTTP GET/GDB-RSP/JSON/NUL) → на всё
  ТИШИНА, соединение не рвётся;
- **passive 45 c** без единой отправки → ТИШИНА (не стример);
- **TLS ClientHello** → handshake timeout (сервер молчит на ClientHello → **НЕ TLS**).

**Вывод**: 33000 — молчаливый бинарный ingest-сервер, который никогда не
говорит первым и ждёт, что клиент пришлёт специфичный протокол/образ. Профиль
(boot-time, one-shot, silent, «читает-ждёт») характерен для внутреннего
OTA/provisioning/диагностик-приёмника. Публичные источники порт не опознают —
вендор-специфика Sber/StarOS.

**Почему запарковано**: блайндом не вскрыть (неизвестный бинарный протокол,
каждая догадка = ребут). Реальные пути — оба вне досягаемости: (1) шелл на
колонке для `/proc/net/tcp` (нет — :4242 без netstat), (2) захват байтов
легитимного клиента (неизвестно, кто и когда к нему коннектится — boot-time
one-shot). Разморозить, если появится один из этих каналов.

### :33000 — время жизни и связь с облаком (2026-07-11)

**Q1 (когда умирает):** НЕ по таймеру. Открывается ~20-25 c после :4242
(раньше — SYN-drop, connect timeout). Проверено: при margin 90 c порт всё ещё
открыт (connect success). Держит соединение (не рвёт за 45 c). «Смерть» —
исключительно от ПЕРВОГО коннекта (one-shot по подключению, не по времени):
висит открытым сколько угодно, пока кто-нибудь не подключится.

**Q2 (звонит ли в облако):** напрямую не захватить — моя машина на Wi-Fi, трафик
колонки в облако (юникаст колонка→AP→роутер) до меня не доходит + client-isolation
на AP. НО дедукция из данных: мы КАЖДЫЙ раз коннектились к :33000 чисто (порт
открыт и нетронут). Если бы облако/локальный клиент подключались к нему на бусте —
был бы refused/гонка. Раз всегда чисто → **в штатной загрузке к :33000 никто не
подключается**. Это не активный облачный хендшейк, а дремлющий ingest (recovery/
factory-инструмент), «оживающий» только при целенаправленном подключении.

Полноценный захват облачного трафика требует in-path (доступ к роутеру/зеркалу или
проводной MITM) — вне текущих возможностей.

### :33000 — сверка по конфигурации StarOS (2026-07-11)

По конфигурации StarOS (`star.json`) явные порты платформы:
- **20000** — WSS voiceTransport (основной, подтверждает наш reverse);
- **22022** — updaterApiPort (WS + TLS + token; ca_chain + access_token.key);
- **9888** — volumeRegulatorPort.

**Порт 33000 в этой конфигурации ОТСУТСТВУЕТ** (проверена конфигурация Mini,
squashfs от 22.11.2022). Ни как значение порта, ни где-либо ещё. Метод рабочий
(нашёл 20000/22022/9888), но образ — от Mini, а не R2/Home.

Выводы: (1) :33000 — R2/Home-специфичный или из более новой прошивки (наша
колонка — R2, fw 26.1.7, с доп. железом Zigbee/Matter/дисплей). (2) :33000 ≠
updater: updater это 22022 и он WS+TLS+token (ответил бы на наш TLS-хендшейк), а
:33000 на TLS молчал. Разморозить при наличии конфигурации именно R2/Home.

### :33000 — сверка по составу прошивки R2/Home (2026-07-11)

Получен образ R2/Home (SBDV-00171, R2 Main Board V1 C3, 256M SPI-NAND «с spare»:
2048+128 OOB на страницу → после снятия OOB чистый 256МиБ; A/B-слоты
boot_a/b, system_a/b, squashfs zstd, fw-эпоха 25.4.3/дек-2025).

**Порт 33000 в R2 литералом НЕТ** — ни строкой, ни как int-байты (0x80e8/0xe880)
в бинарях обоих слотов. Конфиг-порты R2 (star.json): 20000 (WSS), 22022
(updaterApiPort WS+TLS+token), 9888 (volumeRegulator), 80, 5060. 33000 не среди
них → зашит в генерируемом на бусте wrapper'е (`swupdate.sh` в статике отсутствует).

**Но :33000 однозначно R2-эксклюзивный** (у Mini его нет — подтверждено). R2
добавляет (init.d): **S60moira** (`/vendor/moira/r2_hub` — Zigbee-хаб умного дома,
свой protobuf/DSL-протокол в codegen, обновление firmware Zigbee-модуля Telink
B91), **S60matter_controller**, **S80swupdate** (A/B-установщик, верифицирует
ПОДПИСАННЫЕ .swu образы по RSA-ключу `/etc/swupdate/public_key.pem`), adbd, iiod
(=наш :30431).

**Вывод (высокая уверенность):** :33000 — сокет firmware/provisioning-ingest
R2-хаб/апдейт-подсистемы (swupdate-инсталлер и/или обновление Zigbee-модуля через
moira). Это объясняет ВСЮ эмпирику: молчит (ждёт валидный ПОДПИСАННЫЙ бинарный
образ, на мусор/HTTP/TLS не отвечает), one-shot (одна установка за буст → reboot
чтобы «перезарядить»), boot-time, R2-only. Точный демон/порт-литерал статикой не
пробит (порт не в конфиге; wrapper генерится на рантайме). Разморозить при
рантайм-доступе (шелл: `ss -tlnp` → PID→бинарь) или валидном .swu (у нас нет ключа).
