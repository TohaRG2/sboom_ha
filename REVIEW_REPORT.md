# Отчёт о конкурентном двойном code review — sboom_ha

**Дата ревью:** 2026-08-08
**Объект:** Home Assistant custom integration `sboom_ha` (локальное управление колонками SberBoom)
**Формат:** 8 конкурирующих ревьюеров × 4 измерения, адверсариальная верификация каждой находки

---

## 1. Резюме

### Общая оценка кодовой базы

Кодовая база в целом зрелая и дисциплинированная: чистое разделение транспорта (`api.py` без зависимостей от HA), продуманные инварианты (сетевая ошибка ≠ not_found для лирики, один WS на координатор), уже начатая декомпозиция координатора (LyricsManager, CoverManager), хорошее покрытие парсеров тестами. **Критических находок нет, high — одна.** Большинство проблем — это «швы» между подсистемами: рассинхронизированные дубли логики (deeplink, optimistic-обновления, device_id), самодельные парсеры без учёта граничных случаев и незакрытые жизненные циклы ресурсов (панель, ZvukClient, подписки).

### Ключевые риски

1. **Приватность:** диагностика выгружает точные координаты дома, IP и сырой state в bug-report'ы (#3, high).
2. **Безопасность:** SSRF через WS-команду панели — любой аутентифицированный пользователь HA может заставить сервер слать GET во внутреннюю сеть (#2); слабая валидация deeplink-входов панели (#41); WSS без верификации сертификата (#40).
3. **Конкурентность:** last-write-wins гонка poll против push/optimistic на общих полях `coordinator.state`/`track` (#5) и семейство мелких гонок в менеджерах (#33).
4. **Дублирование с расхождением:** три независимые реализации политики optimistic-обновлений (#18), два маппинга zvuk-URL → deeplink (#17), три правила вычисления device_id (#4) — дубли уже разошлись и уже дают наблюдаемые баги.
5. **Молчаливые деградации:** трек со скобками в названии молча выбрасывается (#1), MJPEG-стрим без кадров в BT-режиме (#9), device-триггеры мертвы для manual-entry (#4), вечно закэшированный токен Звука (#6).

### Статистика находок

| Вердикт | Кол-во |
|---|---|
| **CONFIRMED** | 43 |
| **PLAUSIBLE** (нужна ручная проверка) | 1 |
| **REFUTED** (опровергнуты верификацией) | 3 |

Распределение подтверждённых по севиритету:

| Севиритет | Кол-во | ID |
|---|---|---|
| critical | 0 | — |
| high | 1 | #3 |
| medium | 15 | #1, #2, #4, #5, #6, #7, #9, #10, #11, #13, #14, #15, #16, #17, #18 |
| low | 27 | #8, #12, #19, #20, #22, #23, #25–#28, #31–#47 |

Конкурентные совпадения (одну и ту же проблему независимо нашли ≥2 ревьюера): 12 подтверждённых находок, максимум — #31 (3 ревьюера). Такие совпадения — сильный сигнал реальной значимости.

---

## 2. Методология

- **8 конкурирующих ревьюеров** работали независимо по 4 измерениям: корректность (correctness-A/B), конкурентность и гонки (concurrency-A/B), архитектура/SOLID/DRY (architecture-A/B), надёжность и безопасность (robustness-A/B).
- **Дедупликация:** совпадающие находки разных ревьюеров объединены; факт независимого обнаружения зафиксирован в поле «Кто нашёл» — это мера конкурентного подтверждения.
- **Адверсариальная верификация:** каждая находка прошла проверку верификатором, задачей которого было её опровергнуть — с чтением кода, grep'ом по кодовой базе и, где возможно, репродукцией (запуском фрагментов парсеров/регэкспов/PIL на реальных данных). Итоговые вердикты: CONFIRMED (подтверждено кодом/репродукцией), PLAUSIBLE (код подтверждён, но реальное проявление зависит от неизвестных данных прошивки), REFUTED (сценарий недостижим). Севиритет по итогам верификации мог быть скорректирован (обычно — понижен при недостижимости заявленного вектора).

---

## 3. Подтверждённые находки (CONFIRMED)

### 3.1 High

---

#### [#3] Диагностика утекает PII в обход редактирования: raw_state_json, координаты дома, IP

- **Файл:** `custom_components/sboom_ha/diagnostics.py:75` — severity **high**, категория `security-privacy`
- **Кто нашёл:** robustness-B

В diagnostics выгружается `coord.state` через `asdict()` + `async_redact_data(..., TO_REDACT)`. Но: (1) `SpeakerState.raw_state_json` — цельная JSON-строка полного state (внутри `network.ip`, `location.lat/lon`, серийники, device_id) — `async_redact_data` редактирует только ключи dict'ов и не заглядывает внутрь строки; (2) поля `DeviceState.latitude/longitude/location_accuracy` (точные координаты дома) и `network_ip` отсутствуют в `TO_REDACT`; (3) `TrackInfo.raw` выгружается целиком. Пользователь, прикладывающий diagnostics к bug report на GitHub, **публикует геолокацию своего дома**.

**Evidence:**
```python
# diagnostics.py
"state": (async_redact_data(_safe_dataclass(coord.state), TO_REDACT) if coord else None)
# _models.py:87: latitude: float | None = None  # location.lat
# _models.py:69: network_ip: str | None = None
# _models.py:100: raw_state_json: str | None = None
# — ни одного из этих ключей нет в TO_REDACT
```

**Верификация:** парсер реально заполняет latitude/longitude из `location.lat/lon` (`_parsers.py:183-192`), network_ip — из `network.ip`; `async_redact_data` не заглядывает в строковые значения.

**Рекомендация:** добавить `latitude`, `longitude`, `location_accuracy`, `network_ip`, `raw_state_json`, `raw` в `TO_REDACT` (или полностью исключить сырые поля из выгрузки диагностики).

---

### 3.2 Medium

---

#### [#1] Backward-скан к `{` не учитывает скобки внутри JSON-строк — трек с `{`/`}` в названии не парсится

- **Файл:** `custom_components/sboom_ha/_parsers.py:324` — категория `parsing`
- **Кто нашёл:** correctness-A

В `parse_track` поиск начала JSON-объекта идёт через `_scan_open_brace_backward`, который считает баланс `{`/`}` посимвольно, игнорируя строковый контекст (in_str/esc), в отличие от прямого скана `_extract_json_object`. Если в любом строковом поле до `"trackId"` (artists[].name, title) встречается `}` — depth ломается и возвращается −1; если `{` — возвращается позиция внутри строки, и `json.loads` падает. В обоих случаях `parse_track` возвращает None: track-update **молча выбрасывается** и в push-пути (`_handle_event`), и в poll-пути.

**Evidence:**
```python
def _scan_open_brace_backward(s: str, pos: int) -> int:
    depth = 0
    for i in range(pos, -1, -1):
        ch = s[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return i
            depth -= 1
    return -1  # — нет обработки in_str, в отличие от _extract_json_object
```

**Верификация:** репродукция — `parse_track(b'{"artists":[{"id":"7","name":"A}B"}],"trackId":"55",...}')` → None (control-строка без скобок парсится). Севиритет снижен до medium: скобки в названиях — редкий кейс.

**Рекомендация:** добавить в backward-скан отслеживание строкового контекста (in_str/esc), как в `_extract_json_object`, либо парсить от начала payload'а вперёд.

---

#### [#2] SSRF: `sboom/cover_color` скачивает произвольный URL, присланный из фронтенда

- **Файл:** `custom_components/sboom_ha/websocket_api.py:416` — категория `security-ssrf`
- **Кто нашёл:** robustness-B

WS-команда `sboom/cover_color` принимает поле `url` от любого аутентифицированного пользователя HA (`websocket_command` по умолчанию НЕ требует admin) и передаёт его в `ZvukClient.dominant_cover_color`, который делает серверный GET httpx-клиентом с `follow_redirects=True` без проверки хоста/схемы (ожидался только `cdn-image.zvuk.com`). Любой пользователь может заставить сервер HA слать GET во внутреннюю сеть (роутер, 169.254.169.254, LAN), а тело ответа скармливается Pillow `Image.open` (парсинг attacker-controlled данных). Результат кешируется в `_color_cache` по URL без ограничения размера — произвольными URL можно раздувать память.

**Evidence:**
```python
# websocket_api.py
vol.Required("url"): str
color = await zvuk.dominant_cover_color(msg["url"])
# zvuk_client.py:348
resp = await self._http().get(url)   # httpx.AsyncClient(follow_redirects=True, ...)
self._color_cache[url] = color       # без ограничений
```

**Верификация:** подтверждено; SSRF blind (наружу утекает только hex-цвет/факт ошибки) и требует аутентифицированного пользователя HA — потому medium, а не high.

**Рекомендация:** allowlist хостов (только `cdn-image.zvuk.com` + хосты iTunes/Deezer-обложек), запрет не-https, лимит размера тела ответа, ограничение размера `_color_cache` (LRU).

---

#### [#4] Device-триггеры молча не срабатывают для entries, добавленных вручную

- **Файл:** `custom_components/sboom_ha/device_trigger.py:85` — категория `correctness`
- **Кто нашёл:** architecture-B

Фильтр триггера строится из device-identifier'а, который в `_entity_base.py:32` вычисляется как `entry.data.get(CONF_DEVICE_ID) or host` (для manual flow без zeroconf → host). Но события в HA bus несут `device_id = entry.data.get(CONF_DEVICE_ID)` (`coordinator.py:612`), т.е. None для manual-entry. Триггер подписывается на `{"device_id": "<host>"}`, а события приходят с `device_id=None` — все четыре device-триггера (track/playback/volume/connection_changed) **никогда не срабатывают** для вручную добавленной колонки, без единой ошибки в логах. Два слоя используют разные fallback-правила для одного идентификатора.

**Evidence:**
```python
# device_trigger.py
sber_id = _sber_device_id(hass, config[CONF_DEVICE_ID])
if sber_id is not None:
    event_data["device_id"] = sber_id
# coordinator.py:612: "device_id": self.entry.data.get(CONF_DEVICE_ID)  # None для manual flow
# _entity_base.py:32: device_id = entry.data.get(CONF_DEVICE_ID) or host
```

**Верификация:** все три слоя проверены; `config_flow.py:483` — manual flow не заполняет `_device_id`. Medium: затронут только manual-путь онбординга.

**Рекомендация:** единая функция вычисления device_id (с одинаковым fallback на host) — использовать её и в событиях координатора, и в `_entity_base`, и в device_trigger.

---

#### [#5] Last-write-wins гонка: poll перезаписывает более свежие push/optimistic данные state/track

- **Файл:** `custom_components/sboom_ha/coordinator.py:441` — категория `race-condition`
- **Кто нашёл:** concurrency-A

Разделяемые поля `coordinator.state` и `coordinator.track` мутируются из нескольких задач без сериализации: (1) poll-задача в `_refresh_state_and_track` присваивает их ПОСЛЕ await'ов; (2) listener-задача через `_handle_event` по push-событиям; (3) команды через `apply_optimistic_state/track`. Poll-ответ, сформированный до push'а, затирает свежие данные: (а) optimistic volume от слайдера откатывается in-flight `get_state`'ом; (б) push о смене трека затирается poll'ом, стейл висит до следующего интервала, а `_fire_change_events` со стейл prev_track стреляет дублирующиеся `EVENT_TRACK_CHANGED`/`EVENT_PLAYBACK_CHANGED`. Дополнительно `_connect_and_sync` и плановый `_async_update_data` могут выполнять `_refresh_state_and_track` конкурентно.

**Evidence:**
```python
# coordinator.py:441
self.track = self._stamp_track(await self.client.get_metadata())
# :432
self.state = self._merge_state(await self.client.get_state())
# конкурентно: coordinator.py:513–515 (listener) и media_player.py:201 apply_optimistic_state(volume_percent=target)
```

**Верификация:** подтверждено; окно реально, самокорректируется следующим poll'ом (15 с) — medium.

**Рекомендация:** generation/sequence-счётчик: инкрементировать при каждом push/optimistic-обновлении, снимать снапшот перед poll-await'ами и отбрасывать poll-результат, если счётчик изменился; либо сериализовать все мутации через asyncio.Lock/очередь.

---

#### [#6] Анонимный токен Звука кэшируется навсегда — `force=True`/refresh после 401 нигде не вызывается

- **Файл:** `custom_components/sboom_ha/zvuk_client.py:152` — категория `reliability`
- **Кто нашёл:** correctness-A + robustness-A (2 ревьюера независимо)

`ZvukClient.get_token` кэширует токен в инстансе навсегда: параметр `force=True` (докстринг: «например, после 401») не вызывается ни в одном месте кодовой базы (подтверждено grep'ом). `_graphql` и `search` при 401 делают `raise_for_status`; вызывающие гасят `httpx.HTTPError` и возвращают пустой результат. Клиент — синглтон в `hass.data`, живёт до рестарта HA. Когда анонимный токен истекает, поиск в панели, drill-down и `sboom_ha.play_music` с query **молча возвращают «ничего не найдено» до перезапуска Home Assistant**.

**Evidence:**
```python
if self._token and not force:
    return self._token
# grep: get_token(force=True) не вызывается нигде; единственные вызовы — :167 и :380, оба без force
```

**Верификация:** подтверждено grep'ом; оговорка — срок жизни анонимного токена Звука не подтверждён, но сам дефект (мёртвый параметр, отсутствие 401-обработки) реален.

**Рекомендация:** при `httpx.HTTPStatusError` с кодом 401 в `_graphql`/`search` — один retry с `get_token(force=True)`.

---

#### [#7] Push GET_STATE обновляет только volume/device, но не now-playing для радио/Bluetooth

- **Файл:** `custom_components/sboom_ha/coordinator.py:520` — категория `correctness`
- **Кто нашёл:** correctness-A + correctness-B (2 ревьюера независимо)

В poll-пути (`_refresh_state_and_track`) при пустом `get_metadata` трек добывается из GET_STATE через `_track_from_current_state` (радио/BT живут в `background_apps`). В push-пути (`_handle_event`, ветка `OP_GET_STATE`) новый state мержится, но `self.track` из него НЕ пересобирается, а ветка `OP_GET_META_DATA` при отсутствии trackId возвращает None. Итог: при смене песни по Bluetooth или трека на радио title/artist в HA (media_player, караоке-камера, сенсоры, панель) обновятся только на следующем volume-poll'е — задержка до `OPT_VOLUME_POLL_INTERVAL` (по умолчанию 15 с), хотя данные уже пришли push'ем.

**Evidence:**
```python
if OP_GET_STATE in req_data:    # State update
    ...
    self.state = self._merge_state(new_state)
    changed = True
# track_from_state здесь не вызывается; фолбэк
# self.track = self._stamp_track(self._track_from_current_state()) — только в poll-пути
```

**Верификация:** подтверждено; `parse_track` требует regex `"trackId":"\d+"`, которого у BT/радио нет.

**Рекомендация:** в push-ветке `OP_GET_STATE` после мержа state при отсутствии каталожного трека вызывать `_track_from_current_state()` и обновлять `self.track` (с `_stamp_track`).

---

#### [#9] MJPEG-стрим не отдаёт ни одного кадра для трека без позиции (BT)

- **Файл:** `custom_components/sboom_ha/camera.py:246` — категория `correctness`
- **Кто нашёл:** correctness-B

В `_stream_idle` начальное значение `last_sec = None`. Если `track_position()` возвращает None (типичный Bluetooth-источник: title/artist есть, position нет), то `cur_sec = None == last_sec` на каждой итерации, условие `cur_sec != last_sec` никогда не срабатывает, и `_build_idle_jpeg`/`_write_jpeg` не вызываются вообще. Пользователь, открывший камеру (или отправивший её на ТВ) в BT-режиме без позиции, получает **пустой стрим без единого кадра** (клиент висит в ожидании).

**Evidence:**
```python
last_sec: int | None = None
while (...):
    pos = track_position(self.coordinator)
    cur_sec = int(pos) if pos is not None else None
    if cur_sec != last_sec:
        jpeg = await self._build_idle_jpeg()
        ...
    await asyncio.sleep(1)  # при pos=None cur_sec==last_sec==None навсегда
```

**Верификация:** подтверждено; внешний `handle_async_mjpeg_stream` перед `_stream_idle` тоже не пишет кадров.

**Рекомендация:** sentinel-объект вместо None для `last_sec` (например, `object()`), либо безусловная отрисовка первого кадра до цикла.

---

#### [#10] Перенос строк съедает начало текста без пробелов (CJK-лирика, длинные слова)

- **Файл:** `custom_components/sboom_ha/image_render.py:45` — категория `correctness`
- **Кто нашёл:** correctness-B + architecture-B (2 ревьюера независимо)

`_layout_text` разбивает текст регуляркой `re.findall(rf"(.{{1,{line_width}}})(?:\s|$)", text)`: каждый фрагмент обязан заканчиваться пробелом или концом строки. Для текста без пробелов длиннее `line_width` (типичный случай — японская/китайская/корейская лирика из NetEase/lrclib) совпадёт только хвост ≤line_width символов — **начало строки молча отбрасывается**. Существующий тест `test_draw_lyrics_handles_very_long_line` это не ловит (проверяет лишь валидность JPEG).

**Evidence:**
```python
lines = re.findall(rf"(.{{1,{line_width}}})(?:\s|$)", text)
# эксперимент: re.findall(r"(.{1,22})(?:\s|$)", <27 CJK-символов>) → [последние 22 символа]
# 'a'*300 при lw=22 → 1 строка из 22 символов (278 из 300 выброшены)
```

**Верификация:** репродукция подтверждена; для текста с пробелами перенос работает корректно.

**Рекомендация:** заменить регэксп на явный wrap (например, `textwrap.wrap` с `break_long_words=True`) с жёстким разрезанием слов длиннее `line_width`; добавить тест на CJK-строку с проверкой суммарной длины строк.

---

#### [#11] Рекурсия уменьшения шрифта в `_layout_text` уходит в size ≤ 0 → ValueError из PIL

- **Файл:** `custom_components/sboom_ha/image_render.py:47` — категория `correctness`
- **Кто нашёл:** correctness-B + architecture-B (2 ревьюера независимо)

Рекурсия `_layout_text(text, box, anchor, font_size - 10, line_width + 3)` продолжается, пока строк больше 3–4, без нижней границы font_size. Для длинного текста с пробелами (~200–300 символов: длинная строка лирики, название подкаст-выпуска, список артистов коллаба) font_size уходит в 0 и минус, после чего `_font(size)` → `ImageFont.truetype(...)` кидает `ValueError('font size must be greater than 0')`. В MJPEG-стриме это превращается в вечный цикл «ошибка кадра, повтор через 2s», а в snapshot-пути `async_camera_image` исключение не перехватывается — **снапшот камеры падает**.

**Evidence:**
```python
if (font_size > 70 and len(lines) > 3) or (font_size <= 70 and len(lines) > 4):
    return _layout_text(text, box, anchor, font_size - 10, line_width + 3)
# нижней границы нет; репро: layout('слово '*50, 90, 22) → font_size=-100
# ImageFont.truetype(DejaVuSans.ttf, 0) → ValueError
```

**Верификация:** алгоритм воспроизведён, ValueError из PIL подтверждён; `async_camera_image` (camera.py:93-105) без try/except.

**Рекомендация:** нижняя граница рекурсии (`if font_size <= 12: return lines, font_size` с обрезкой числа строк), плюс try/except вокруг рендера в `async_camera_image`.

---

#### [#13] При delta ≥ 600 c экстраполяция отбрасывается целиком — позиция длинного трека откатывается назад

- **Файл:** `custom_components/sboom_ha/helpers.py:41` — категория `extrapolation`
- **Кто нашёл:** correctness-A

`track_position()` при дельте больше `_MAX_EXTRAPOLATION_SEC` (600 c) не clamp'ит дельту, а полностью её игнорирует. Защита задумывалась от мусорного `position_ts_ms` после reboot, но применяется и к `received_monotonic` — HA-стороннему monotonic-штампу, который мусорным быть не может. `coordinator._stamp_track` сознательно переносит старый `received_monotonic` между poll'ами. Итог: на треке длиннее ~10 минут без промежуточных push-событий (подкаст, аудиокнига) на 599-й секунде позиция = base+599, а на 601-й экстраполяция отключается и позиция **резко откатывается к base**. Камера/караоке и сенсоры прыгают на ~10 минут назад, при этом media_player (через `media_position_updated_at` на стороне frontend, без этого капа) продолжает идти вперёд — рассинхрон двух представлений позиции.

**Evidence:**
```python
if delta is not None and 0 <= delta < _MAX_EXTRAPOLATION_SEC:
    speed = track.playback_speed or 1.0
    ...
    pos += delta * speed  # при delta >= 600 прибавка исчезает целиком, а не ограничивается
```

**Верификация:** подтверждено, включая рассинхрон с `media_player.media_position_updated_at`.

**Рекомендация:** clamp вместо отбрасывания (`delta = min(delta, _MAX_EXTRAPOLATION_SEC)`), кап применять только к подозрительному `position_ts_ms`-пути, не к monotonic.

---

#### [#14] Жизненный цикл боковой панели не согласован между entries и не снимается при unload

- **Файл:** `custom_components/sboom_ha/__init__.py:106` — категория `resource-lifecycle`
- **Кто нашёл:** concurrency-B + robustness-B (2 ревьюера независимо)

Панель регистрируется через глобальные маркеры в `hass.data`, из-за чего: (1) при двух колонках entry с `panel_enabled=False` при своей перезагрузке безусловно удаляет панель (`async_remove_panel`), которую зарегистрировала и всё ещё хочет другая entry; (2) `async_unload_entry` вообще не удаляет панель и маркеры — после удаления интеграции пункт «SberBoom» остаётся в боковом меню до рестарта HA, ссылаясь на мёртвый backend; (3) в блоке регистрации статики между проверкой маркера и его установкой есть await'ы — при параллельном setup двух entries статика регистрируется дважды (TOCTOU).

**Evidence:**
```python
# __init__.py:105-108
if not entry.options.get(OPT_PANEL_ENABLED, ...):
    if hass.data.pop(marker, None):
        async_remove_panel(hass, PANEL_URL_PATH)
# async_unload_entry (132-145): нет ни async_remove_panel, ни очистки {DOMAIN}_panel_registered
```

**Верификация:** все три пункта подтверждены построчно.

**Рекомендация:** refcounting: панель существует, пока есть хотя бы один загруженный entry с `panel_enabled=True`; пересчитывать при setup/unload/options-update; удалять панель и маркеры при выгрузке последнего entry; регистрацию статики защитить установкой маркера до await'ов.

---

#### [#15] `Cli4242Client._drain` всегда дожидается полного read-timeout — каждый CLI-опрос стоит ~3.6 с внутри цикла координатора

- **Файл:** `custom_components/sboom_ha/cli4242.py:138` — категория `efficiency`
- **Кто нашёл:** concurrency-B + architecture-B (2 ревьюера независимо)

CLI :4242 держит соединение открытым и не шлёт EOF/терминатор, а `_drain` читает в цикле до TimeoutError: после последнего чанка всегда сжигается полный `_READ_TIMEOUT` (3.0 c) плюс 0.6 c на «проглатывание» приветствия — каждая CLI-команда занимает минимум ~3.6 c. В `coordinator._poll_hw` на каждом 20-м тике последовательно выполняются `zigbee list` и `matter list` — это ~7+ секунд **внутри** `_refresh_state_and_track`: очередной volume-poll и обновление entities задерживаются дольше самого интервала. Тот же штраф при старте entry в `_probe_hw_capabilities` (задержка setup ~7–11 c на Zigbee/Matter-моделях).

**Evidence:**
```python
# cli4242.py:150-159
while True:
    chunk = await asyncio.wait_for(reader.read(4096), timeout)
    if not chunk:
        break        # недостижим при живом соединении
    buf += chunk
# coordinator.py:474-478: await self._poll_hw() внутри _refresh_state_and_track
```

**Верификация:** подтверждено; уточнение — стартовый probe идёт частично параллельно (gather, ~3.6 с), но суммарная задержка setup всё равно ~7–11 с.

**Рекомендация:** детектировать конец таблицы/prompt CLI для раннего выхода или снизить таймаут; архитектурно — вынести hw-опрос в отдельный цикл со своим темпом (см. #20), не блокируя WS-координатор.

---

#### [#16] `_deeplink.py` работает через приватные внутренности SberSpeakerClient (`_ws`, `_pending`, `_lock`)

- **Файл:** `custom_components/sboom_ha/_deeplink.py:106` — категория `architecture`
- **Кто нашёл:** architecture-A + architecture-B (2 ревьюера независимо)

`send_server_action` напрямую использует `client._ws`, `client._pending`, `client._lock` — повторяет корреляцию request/response и сериализацию отправки снаружи класса. Прямое нарушение инварианта CLAUDE.md («Не вызывай приватные методы клиента»). Любое изменение внутренностей `api.py` молча сломает `play_deeplink`/`send_server_action` — сервис `play_music`, WS-команду `sboom/play` и панель; тесты `api.py` этого не поймают, т.к. контракт неявный.

**Evidence:**
```python
# _deeplink.py:106-118
ws = client._ws
...
client._pending[msg_id] = fut
async with client._lock:
    await ws.send(envelope)
...
finally:
    client._pending.pop(msg_id, None)
```

**Верификация:** подтверждено; docstring модуля декларирует это как сознательное решение («БЕЗ модификации api.py»), что не отменяет нарушение инварианта.

**Рекомендация:** публичный метод `client.send_raw_request(envelope, msg_id, timeout)` или сразу `client.server_action(name, payload)` рядом с `_request_response`, инкапсулирующий `_pending`/`_lock`/`_ws`; `_deeplink.py` сделать тонкой обёрткой.

---

#### [#17] Логика zvuk-URL → deeplink продублирована в websocket_api.py и zvuk_client.py

- **Файл:** `custom_components/sboom_ha/websocket_api.py:49` — категория `dry`
- **Кто нашёл:** architecture-A + architecture-B (2 ревьюера независимо)

Две независимые реализации маппинга «kind → pt / tid|pid» и сборки staros-deeplink: в websocket_api.py (`_PT_TID`, `_ZVUK_URL_KIND_TO_PT`, `_build_deeplink`, `_deeplink_from_zvuk_url`) и в zvuk_client.py (`_URL_KIND_MAP`, `_URL_RE`, `parse_zvuk_url`, `build_deeplink`). Они уже разошлись по поведению: zvuk_client валидирует id регэкспом (только цифры), панельная версия берёт `segments[-2:]` из urlparse без проверки. Плюс в `search_first_deeplink` решение tid/pid продублировано инлайном дважды. При добавлении нового kind придётся править 3 места.

**Evidence:**
```python
# websocket_api.py:45-55
_ZVUK_URL_KIND_TO_PT = {"track": "track", ..., "abook": "podcast"}
# против zvuk_client.py:75-84
_URL_KIND_MAP = {"track": ("track", "tid"), "artist": ("artist", "pid"), ...}
```

**Верификация:** подтверждено; `services._resolve_deeplink` уже использует zvuk_client-версию — единый источник возможен.

**Рекомендация:** единственный источник — статические `ZvukClient.parse_zvuk_url`/`build_deeplink`; использовать их из `websocket_api.ws_play`. Попутно закрывается часть #41 (валидация).

---

#### [#18] Политика optimistic-обновлений продублирована и непоследовательна: media_player/websocket_api против switch/number

- **Файл:** `custom_components/sboom_ha/websocket_api.py:481` — категория `consistency`
- **Кто нашёл:** architecture-A + architecture-B (2 ревьюера независимо)

Маппинг «команда → optimistic-патч состояния» существует в двух местах: методы SboomMediaPlayer и `websocket_api._apply_optimistic` (тот же список if/elif на 30 строк), и они уже разошлись: `media_player.async_set_repeat` НЕ делает optimistic-патч, а панельный `"repeat"` — делает; volume-команды в media_player дополнительно вызывают `async_request_refresh`, панельные — нет. Хуже: `SboomSwitchEntity` (mute) и `SboomVolumeNumber` (volume) после команды делают только `async_request_refresh` БЕЗ optimistic-патча — переключатель mute и слайдер громкости визуально «отпрыгивают» в старое значение (проявляется при повторных переключениях в окне debounce-cooldown). Один паттерн подтверждения команд реализован в трёх местах тремя способами.

**Evidence:**
```python
# websocket_api._apply_optimistic:
if action == "play": coordinator.apply_optimistic_track(playing=True) ...
# switch.py:75-80: await self._run_command(...); await self.coordinator.async_request_refresh()  # без optimistic
# media_player.py:245-249: apply_optimistic_state(muted=mute); await self.coordinator.async_request_refresh()
# number.py:45-49 — тоже без optimistic
```

**Верификация:** все три места подтверждены; оговорка — `request_refresh` immediate-first, «отпрыгивание» проявляется в основном в окне debounce.

**Рекомендация:** единый командный слой на координаторе — `coordinator.async_execute(action, value)`: вызов клиента + optimistic-патч + политика refresh в одном месте; entity и ws_command — тонкие адаптеры.

---

### 3.3 Low

---

#### [#8] `play_music` с kind=podcast всегда падает: схема разрешает, маппинг — нет

- **Файл:** `custom_components/sboom_ha/services.py:58` — `correctness` — нашёл correctness-B

Схема сервиса разрешает `kind ∈ {track, artist, release, playlist, podcast, abook}`, но в `_URL_KIND_MAP` ключа `"podcast"` нет. Вызов с `id` и `kind="podcast"` проходит валидацию, но `parse_zvuk_url` возвращает None, и `_resolve_deeplink` кидает ServiceValidationError, советующий ровно то значение, которое пользователь уже указал. Верификация: подтверждено; low — есть workaround (`kind=abook` маппится в `pt=podcast`), ошибка видима.
**Рекомендация:** добавить `"podcast": ("podcast", "pid")` в `_URL_KIND_MAP` либо убрать `podcast` из схемы.

#### [#12] `seek_to` не clamp'ит отрицательную позицию, а varint кодирует отрицательные числа мусором

- **Файл:** `custom_components/sboom_ha/api.py:369` — `boundary` — нашёл correctness-A

`_tlv.varint(-1)` даёт `b'\x7f'` (колонка декодирует как 127). `seek_to` — единственная числовая команда без clamp (`set_volume` — 0..100, `set_playback_speed` — 0.5..2.0). Верификация: репродукция подтверждена (`decode(field(1,0,-1)) == {1: 127}`), но заявленный вектор через `media_player.media_seek` неточен — схема HA использует `cv.positive_float`; реальный незащищённый путь — только ручная WS-команда `sboom/command action=seek` (схема `vol.Any(int,...)` без Range) → low.
**Рекомендация:** `max(0, int(position_sec))` в `seek_to` + `vol.Range(min=0)` в WS-схеме.

#### [#19] Инфраструктурные хелперы (singleton ZvukClient, поиск координаторов) скопированы между модулями

- **Файл:** `custom_components/sboom_ha/services.py:126` — `dry` — нашли architecture-A + architecture-B (2 ревьюера)

Ленивый singleton ZvukClient реализован дважды (services и websocket_api, включая дубль константы `_ZVUK_CLIENT_KEY` — единственность инстанса держится на совпадении строк). Поиск координаторов реализован трижды (services / websocket_api / device_action, причём с разными API: `async_entries` vs `async_loaded_entries`). Верификация: подтверждено; сегодня работает корректно → low.
**Рекомендация:** вынести `get_zvuk_client(hass)` и `iter_coordinators(hass)` в общий HA-зависимый модуль (например, `_ha_helpers.py`; `helpers.py` трогать нельзя — он чистый Python).

#### [#20] Аппаратная подсистема (iio/Zigbee/Matter) вросла в SboomCoordinator — тренд к god object

- **Файл:** `custom_components/sboom_ha/coordinator.py:192` — `srp` — нашёл architecture-A

Координатор (665 строк) оброс третьей подсистемой: `_probe_hw_capabilities`, `_poll_hw`, `_poll_matter` + 9 полей (`_iio_client`, `_cli`, `iio_cap`, `has_zigbee_cli`, `has_matter_cli`, `iio_reading`, `zigbee_devices`, `matter_devices`, `matter_raw`) + `_hw_poll_tick`. Это отдельная ответственность с другими транспортами (libiio XML, debug-CLI :4242). Верификация: факты сходятся; архитектурное наблюдение без failure-сценария → low.
**Рекомендация:** выделить `HwMonitor` по образцу LyricsManager (`async_probe()`, `async_poll()`, on_update-callback); сенсоры читают `coordinator.hw.*`. Связка с #15 усиливает мотивацию (свой темп опроса).

#### [#22] `search()`: `get_token` вне try — сетевая ошибка Звука пробивает контракт «ошибка → пустой результат»

- **Файл:** `custom_components/sboom_ha/zvuk_client.py:380` — `error-handling` — нашёл robustness-A

Вызов `get_token()` стоит ДО try: при недоступности zvuk.com httpx-исключение вылетает из `search()`. В панели прикрыто broad-except, но в цепочке `services.py:156 → search_first_deeplink → search` не ловится — сырое исключение вместо ServiceValidationError. Верификация: подтверждено; страдает только качество ошибки → low.
**Рекомендация:** внести `get_token()` внутрь try либо обернуть цепочку в services в `try/except httpx.HTTPError → HomeAssistantError` (см. #37).

#### [#23] `_request_get` (Lrclib) не ловит невалидный JSON в 200-ответе

- **Файл:** `custom_components/sboom_ha/lyrics_client.py:124` — `error-handling` — нашёл robustness-A

В except перечислены только `(TimeoutError, aiohttp.ClientError)`. 200 + `application/json` с битым телом → `json.JSONDecodeError` мимо except; валидный JSON-массив → AttributeError на `data.get()` в `_fetch_lrclib`. В lyrics_manager вокруг `fetch_lyrics` только try/finally без except — «unhandled exception in background task». Верификация: подтверждено с уточнением — сценарий captive portal преувеличен (ContentTypeError — наследник ClientError, ловится); остаются реальные, но маловероятные дыры → low.
**Рекомендация:** добавить `ValueError` в except и isinstance-проверку `dict` перед `data.get()`.

#### [#25] `parse_track` находит только trackId в виде строки цифр без пробелов — числовой trackId не парсится

- **Файл:** `custom_components/sboom_ha/_parsers.py:348` — `parsing` — нашёл correctness-A

Regex `r'"trackId":"\d+"'` требует кавычки и отсутствие пробела, при том что `track_from_state` в этом же файле сознательно поддерживает числовой trackId. Push с `"trackId":55` молча отбросится. Верификация: репродукция подтверждена; реальные push'и (по фикстурам) всегда с кавычками — defensive-gap → low.
**Рекомендация:** ослабить regex до `r'"trackId"\s*:\s*"?\d+"?'`.

#### [#26] `decode_repeated`: fixed32 без проверки границ — обрезанный payload даёт битые данные

- **Файл:** `custom_components/sboom_ha/_tlv.py:141` — `boundary` — нашёл correctness-A

В `decode()` ветка kind==5 проверяет `if i + 4 > n: return out`, в `decode_repeated` — нет: обрезанный вход попадёт в результат как «значение». Ветка kind==2 в обоих декодерах молча принимает `ln` больше остатка. Через `decode_repeated` идёт разбор BT-списков. Верификация: репродукция подтверждена; ущерб смягчён isinstance-фильтрами в `_parse_bt_devices` → low.
**Рекомендация:** выровнять `decode_repeated` c `decode()` (граничные проверки для kind 5 и 2).

#### [#27] `parse_state` ищет первую `{` по всему сырому кадру — байт 0x7b в бинарном TLV-префиксе ломает извлечение JSON

- **Файл:** `custom_components/sboom_ha/_parsers.py:290` — `parsing` — нашёл correctness-A

`s.find("{")` может зацепиться за служебный байт 0x7b (например, финальный байт varint-длины, вероятность ~1/128 на varint) — json-путь падает, разбор сваливается в regex-fallback: volume уцелеет, но весь DeviceState (~20 сенсоров) для этого цикла теряется. Верификация: репродукция подтверждена; эффект транзиентный (следующий poll через 5 с) → low.
**Рекомендация:** искать `{` от известной позиции payload'а либо пробовать следующую `{` при неудаче `json.loads`.

#### [#28] `parse_state`: `int(volume["percent"])` без проверки типа — мусорный payload превращается в «колонка недоступна»

- **Файл:** `custom_components/sboom_ha/_parsers.py:306` — `input-validation` — нашёл robustness-A

Единственное незащищённое место в аккуратно isinstance-защищённом парсере: percent=null/строка → TypeError/ValueError. В `_connect_and_sync` `get_state` используется как строгая проверка соединения — исключение из парсера → ConfigEntryNotReady / вечный reconnect при живой колонке. Верификация: репродукция подтверждена; триггер требует аномального payload → low.
**Рекомендация:** `if isinstance(volume.get("percent"), (int, float))`.

#### [#31] Синглтон ZvukClient (httpx.AsyncClient) никогда не закрывается — `aclose()` мёртвый код; `_color_cache` растёт неограниченно

- **Файл:** `custom_components/sboom_ha/websocket_api.py:91` — `resource-lifecycle` — нашли concurrency-B + architecture-A + architecture-B (**3 ревьюера — максимум совпадений**)

`aclose()` определён (zvuk_client.py:141), но по grep не имеет ни одного вызова — клиент и пул соединений живут до конца процесса; `_color_cache` — обычный dict без eviction. Несимметрично с аккуратным lifecycle остального кода. Верификация: grep подтверждён полностью.
**Рекомендация:** listener на `EVENT_HOMEASSISTANT_STOP` с `await client.aclose()` и/или закрытие при выгрузке последнего entry; `_color_cache` — LRU с лимитом (закрывает и часть #2).

#### [#32] Подписка панели (`sboom/subscribe`) переживает reload entry и остаётся на мёртвом координаторе

- **Файл:** `custom_components/sboom_ha/websocket_api.py:248` — `stale-reference` — нашёл concurrency-B

`ws_subscribe` привязывает listener к конкретному экземпляру координатора; при reload entry (смена опций) создаётся новый координатор, а подписка висит на старом: панель «замирает» до перезагрузки страницы (fallback-поллинг фронтенда включается только если subscribe упал при установке), замыкание удерживает весь старый координатор в памяти. Верификация: подтверждено, включая фронтенд (`sboom-feed-base.js:55-72`).
**Рекомендация:** резолвить координатор по entry_id на каждом `_forward` либо слать терминальное событие из `coordinator.async_stop`.

#### [#33] Гонка конкурентных volatile-fetch: единственный слот перезаписывается в порядке завершения задач

- **Файл:** `custom_components/sboom_ha/lyrics_manager.py:162` — `race-condition` — нашёл concurrency-B

Для BT/радио LyricsManager (и аналогично `CoverManager._fetch`) держит один волатильный слот, который каждая завершившаяся задача перезаписывает безусловно. При смене треков A→B поздний A может переписать слот, `current_for(B)` вернёт None → повторный сетевой fetch, мерцание карточки. Верификация: подтверждено в обоих менеджерах; самовосстанавливается → low.
**Рекомендация:** перед записью сверять key с текущим треком координатора либо отменять предыдущую in-flight задачу.

#### [#34] `fallback_cover` выполняет блокирующий файловый I/O прямо в event loop

- **Файл:** `custom_components/sboom_ha/camera.py:280` — `blocking-io` — нашёл concurrency-B

Первый вызов проваливается в `_fallback_bgs()`: `os.path.isdir`, `os.listdir`, чтение четырёх JPG в event loop. Кэшируется `lru_cache(maxsize=1)` — блокировка одноразовая, но детектор блокирующих вызовов HA 2024.x+ выдаст предупреждение. Верификация: подтверждено.
**Рекомендация:** обернуть первый вызов в `asyncio.to_thread` (остальной PIL-рендер уже так делает).

#### [#35] Панель не получает обложку BT/радио: нет фолбэка на `coordinator.current_cover()`

- **Файл:** `custom_components/sboom_ha/websocket_api.py:127` — `correctness` — нашёл correctness-B

`_serialize_track` кладёт только `cover_url(track)` (None для не-zvuk), тогда как media_player и camera используют `cover_url(track) or coordinator.current_cover()`. В панели для BT/радио нет ни обложки, ни ambient-glow. Верификация: подтверждено; фикс тривиален (`coordinator` доступен в `_state_payload`).
**Рекомендация:** добавить тот же фолбэк в `_serialize_track`.

#### [#36] Панель экстраполирует позицию от часов колонки (`position_ts_ms`), а не от часов HA

- **Файл:** `custom_components/sboom_ha/websocket_api.py:119` — `correctness` — нашёл correctness-B

Бэкенд намеренно ушёл от `position_ts_ms` из-за рассинхрона часов (см. `received_ts`/`track_position`, сенсор clock_skew), но в панель сериализуется только `position_ts_ms`, и `sboom-nowplaying.js` сравнивает его с `Date.now()` браузера: прогресс/скраббер уезжает на величину skew, при часах колонки «в будущем» экстраполяция молча отключается. Тот же класс бага, уже починенный для media_player. Верификация: подтверждено.
**Рекомендация:** сериализовать `received_ts` (HA-часы) и считать elapsed от него.

#### [#37] `play_music`/`bluetooth_device`: сетевые ошибки колонки не обёрнуты в HomeAssistantError

- **Файл:** `custom_components/sboom_ha/services.py:171` — `error-handling` — нашёл robustness-A

`play_deeplink`/`bt_device_command` могут бросить `RuntimeError("not connected")`, `TimeoutError`, `ConnectionError` — не перехвачены: сервис падает сырым traceback («Unknown error» в UI). Для сравнения `ws_play` те же ошибки ловит. Верификация: подтверждено.
**Рекомендация:** `try/except (RuntimeError, TimeoutError, ConnectionError) → raise HomeAssistantError(...)` в обоих handler'ах.

#### [#38] `dominant_cover_color` кэширует None при транзиентной сетевой ошибке

- **Файл:** `custom_components/sboom_ha/zvuk_client.py:357` — `caching` — нашёл robustness-A

При `httpx.HTTPError` `color=None` пишется в `_color_cache` — после восстановления сети цвет для этой обложки не будет вычислен до рестарта HA. Нарушает принцип «сетевая ошибка ≠ not_found» (инвариант №6 CLAUDE.md, соблюдённый в lyrics). Верификация: подтверждено.
**Рекомендация:** кэшировать только успех и отрицательный результат декодирования картинки; сетевые сбои — не кэшировать.

#### [#39] Idle-стрим камеры при недоступном CDN повторяет скачивание обложки на каждом кадре

- **Файл:** `custom_components/sboom_ha/camera.py:274` — `network-degradation` — нашёл robustness-A

`_fetch_cover_raw` кэширует только успех; при лежащем CDN каждый кадр (раз в секунду) запускает `_download_cover` с таймаутом 10 с: стрим деградирует до кадра раз в ~10 с + непрерывный поток запросов к лежащему CDN. Верификация: подтверждено.
**Рекомендация:** негативный кэш неудачного URL на N секунд с отдачей `fallback_cover` без сетевого похода.

#### [#40] WSS без верификации сертификата и без пиннинга — pair-токен по неаутентифицированному TLS

- **Файл:** `custom_components/sboom_ha/api.py:147` — `security-network` — нашёл robustness-A

`check_hostname=False`, `CERT_NONE`: атакующий в LAN (ARP-spoofing) может выдать себя за колонку и перехватить `pin_access_token`. Для self-signed сертификата вынужденно, но трейдофф нигде не зафиксирован. Верификация: подтверждено; типичный компромисс локальных интеграций, атака требует активного MITM → low.
**Рекомендация:** TOFU-пиннинг — при pair сохранить fingerprint сертификата в config entry и сверять при подключениях; зафиксировать трейдофф в README.

#### [#41] `sboom/play` собирает deeplink из невалидированных pt/content_id/url (инъекция параметров)

- **Файл:** `custom_components/sboom_ha/websocket_api.py:144` — `security-validation` — нашёл robustness-B

В отличие от сервисного пути (regex + isdigit в zvuk_client), панельный путь не валидирует входы: `_build_deeplink` подставляет произвольные `pt`/`content_id` в f-string без экранирования (значение `123&foo=bar` инъецирует query-параметры), `_deeplink_from_zvuk_url` не проверяет хост и числовость id, поле `deeplink` принимает произвольную staros://-строку — всё уходит на колонку от имени любого не-админ пользователя. Верификация: подтверждено; влияние ограничено локальной колонкой → low.
**Рекомендация:** выровнять с services.py: whitelist pt, `\d+` для id, проверка хоста zvuk.com, URL-квотирование. Естественно закрывается рефакторингом #17.

#### [#42] `_async_update_data` никогда не сообщает об ошибке — `last_update_success` всегда True

- **Файл:** `custom_components/sboom_ha/coordinator.py:658` — `error-handling` — нашёл architecture-B

Все сбои полла проглатываются широкими `except Exception` с DEBUG-логом; `UpdateFailed` не бросается никогда — контракт DataUpdateCoordinator обойдён самодельным флагом `connected`. Побочный эффект: `diagnostics.py:46` выгружает `last_update_success`, который всегда True даже у мёртвой колонки — дезинформирует. Верификация: подтверждено; availability entities корректно работает через `coordinator.connected` → low.
**Рекомендация:** либо убрать поле из diagnostics (заменив на `connected`), либо честно транслировать сбои через `UpdateFailed`.

#### [#43] Мёртвый код: AuthError, get_scanned_bt_devices, draw_cover/draw_lyrics, matter_count, matter_raw, SpeakerState.track

- **Файл:** `custom_components/sboom_ha/api.py:101` — `dead-code` — нашёл architecture-B

Grep подтвердил все 6 пунктов: `AuthError` нигде не бросается; `get_scanned_bt_devices` (op=21) не вызывается; `image_render.draw_cover`/`draw_lyrics` заменены yandex-версиями и не используются; `cli4242.matter_count` живёт только в тестах; `coordinator.matter_raw` пишется, но не читается; `SpeakerState.track` никогда не присваивается и не читается.
**Рекомендация:** удалить (или пометить как экспериментальный API с комментарием, если op=21 планируется к использованию).

#### [#44] Чтение приватных символов чужих модулей: `_synthetic_key`, `coord._stopping`; типизация Any ради тест-стабов

- **Файл:** `custom_components/sboom_ha/sensor.py:23` — `encapsulation` — нашли architecture-A + architecture-B (2 ревьюера)

(1) sensor.py импортирует `_synthetic_key` из lyrics_manager; (2) diagnostics.py читает `coord._stopping`; (3) в `SboomSensorSpec` поля state_class/device_class — `Any` со строковыми литералами `"measurement"`/`"timestamp"`/`"enum"` по коду — опечатку не поймают ни типы, ни тесты. Тот же класс проблем, что уже фиксировали в 0.5.1. Верификация: все три пункта подтверждены.
**Рекомендация:** публичные `LyricsManager.supports_track(track)` и `coordinator.stopping`; строки заменить на enum'ы SensorStateClass/SensorDeviceClass (импорт в стабы решается TYPE_CHECKING/локальными импортами).

#### [#45] `draw_lyrics_with_cover` и `draw_cover_yandex` дублируют рендер source/футера/времени/прогресса

- **Файл:** `custom_components/sboom_ha/image_render.py:358` — `dry` — нашёл architecture-A

Обе функции повторяют блок из четырёх частей (плашка source, footer title/artist, время, `_draw_progress`), уже разошлись в размерах шрифтов (title 46 vs 50, artist 32 vs 36 — намеренность не зафиксирована). Верификация: подтверждено дословно.
**Рекомендация:** вынести `_draw_frame_chrome(ctx, source, title, artist, position_sec, duration_sec, progress, *, title_size, artist_size)`.

#### [#46] Хелпер `_dev()` продублирован в sensor.py и binary_sensor.py (третья копия — `SboomEntity.device_state`)

- **Файл:** `custom_components/sboom_ha/binary_sensor.py:38` — `dry` — нашёл architecture-A

Функция `def _dev(c): return c.state.device if c.state else None` дословно повторена в sensor.py:53-55 и binary_sensor.py:38-40; эквивалент третьим экземпляром — `SboomEntity.device_state`. Верификация: подтверждено.
**Рекомендация:** метод `SboomCoordinator.device_state()` (или функция в общем модуле), три места сводятся к одному.

#### [#47] LyricsManager: `_fetch`/`_fetch_volatile` и ключ title|artists почти полностью дублируются (в т.ч. с CoverManager)

- **Файл:** `custom_components/sboom_ha/lyrics_manager.py:146` — `dry` — нашёл architecture-A

`_fetch` и `_fetch_volatile` повторяют скелет (вызов fetch_lyrics, ранний return при None, запись, `_on_update()`, finally-discard из `_inflight`); `_schedule_*_fetch` дублируют создание task'ов; ключ `f"{title}|{','.join(artists)}".lower()` продублирован между `lyrics_manager._synthetic_key` и `cover_manager._cover_key`. Верификация: подтверждено; нюанс — guard-логика ключей различается (RADIO vs каталожные id), слияние потребует параметризации.
**Рекомендация:** общий приватный `_run_fetch(key, store_result)` внутри LyricsManager; общий helper `track_identity_key(track)` для обоих менеджеров.

---

## 4. Правдоподобные находки (PLAUSIBLE) — требуют ручной проверки

#### [#24] RSSI из op=21 читается как сырой varint — отрицательный dBm станет гигантским положительным числом

- **Файл:** `custom_components/sboom_ha/_parsers.py:575` — severity low, категория `parsing` — нашёл correctness-A

`_parse_bt_devices` при `with_rssi=True` берёт поле 3 напрямую из TLV-декодера без sign-extension/zigzag. Если прошивка кодирует RSSI стандартным protobuf-varint для int32/int64 (two's complement), декодер вернёт число вида 18446744073709551558 для −58 dBm (воспроизведено на декодере). **Но фактическая кодировка прошивки не подтверждена:** единственный тест использует синтетическое rssi=200, реальный сэмпл op=21 пуст. Если прошивка шлёт положительное значение (abs dBm или индекс), бага нет.

**Что сделать:** снять live-сэмпл op=21 с реальной колонки при активном BT-скане и посмотреть сырое значение поля 3. Если оно >2³¹ — добавить two's-complement sign-extension (и/или zigzag) при декодировании.

---

## 5. Опровергнутые находки (REFUTED)

Важно: эти находки выглядят правдоподобно по коду, но верификация показала, что заявленные сценарии недостижимы. **Не «чините» их без переосмысления** — можно внести регрессию или бесполезный код.

| ID | Заголовок | Кто нашёл | Почему опровергнута |
|---|---|---|---|
| #21 | `device_action._coordinator` берёт первый runtime_data любого config entry устройства без проверки домена/типа (device_action.py:60) | architecture-B, robustness-B | Сценарий «чужой координатор» недостижим: `_entity_base.py` регистрирует DeviceInfo ТОЛЬКО с `identifiers={(DOMAIN, device_id)}` и без connections (grep по всем модулям). Merge устройства с entry другой интеграции возможен только через пересечение connections/identifiers, а пересечься с доменным кортежем `(sboom_ha, id)` чужая интеграция не может. `device.config_entries` содержит только entries sboom_ha; у выгруженного entry HA 2024.12+ runtime_data очищен → корректный InvalidDeviceAutomationConfig. isinstance-проверка была бы лишь косметическим hardening. |
| #29 | `close()` отменяет listener-task без await — его finally может отравить следующую сессию (api.py:186) | concurrency-A, concurrency-B | В однопоточном asyncio `task.cancel()` немедленно ставит задачу в ready-queue — её finally выполнится на ближайшей итерации loop, а между `close()` и завершением нового `connect()` всегда лежат `await ws.close()`, backoff-sleep ≥1 с и многоитерационный TLS/WS-handshake. Чтобы finally сработал после `disconnected.clear()` нового connect, отменённая задача должна не планироваться >1 с при живом loop — тогда и connect не продвинулся бы. Остаётся только стилистический нюанс (канонично `cancel(); with suppress(CancelledError): await task`), не баг. |
| #30 | Cleanup в `async_setup_entry` не срабатывает при CancelledError — утечка supervisor-task и WS (`__init__.py:70`) | concurrency-A | Факт кода верен (`except Exception` не ловит CancelledError), но утечка не реализуется: единственный реалистичный источник отмены setup-таска — остановка HA (reload/remove entry в SETUP_IN_PROGRESS бросают OperationNotAllowed, а не отменяют). При shutdown HA сам отменяет задачи из `hass.async_create_background_task` — т.е. supervisor; в его теле `except CancelledError: raise` проходит через finally с `await self.client.close()`, закрывающим WS и listener. Замена на `except BaseException` — чистый hygiene. |

---

## 6. Архитектурные рекомендации

Обобщение подтверждённых находок сводится к пяти системным темам:

### 6.1 Единый командно-состоятельный слой на координаторе

Находки #5, #18: и гонка poll/push/optimistic, и три разные политики optimistic-обновлений — следствие того, что мутации `coordinator.state/track` размазаны по poll-задаче, listener-задаче, media_player, websocket_api, switch и number. Решение одно на обе проблемы:

- `coordinator.async_execute(action, value)` — единственная точка «команда → вызов клиента → optimistic-патч → политика refresh»; entity и ws-команды — тонкие адаптеры.
- Generation-счётчик состояния: push/optimistic инкрементируют; poll снимает снапшот до await'ов и отбрасывает результат, если счётчик изменился.

### 6.2 Единый источник истины для deeplink и идентификаторов

Находки #4, #17, #19, #41: дубли, которые уже разошлись и уже дали баги (мёртвые триггеры, невалидируемый панельный путь). Рефакторинг:

- Deeplink: только `ZvukClient.parse_zvuk_url`/`build_deeplink` (с валидацией) — использовать из `websocket_api.ws_play`, как уже делает services. Заодно закрывается инъекция #41.
- device_id: одна функция с единым fallback (`CONF_DEVICE_ID or host`) для `_entity_base`, событий координатора и device_trigger.
- Новый `_ha_helpers.py`: `get_zvuk_client(hass)`, `iter_coordinators(hass)` (и listener на `EVENT_HOMEASSISTANT_STOP` для `aclose()` — #31).

### 6.3 Выделение HwMonitor и развязка темпов опроса

Находки #15, #20: hw-подсистема (iio/Zigbee/Matter) — чужая ответственность внутри WS-координатора, и её медленный CLI (~3.6 с на команду) блокирует основной цикл. Выделить `HwMonitor` по уже принятому в проекте образцу LyricsManager/CoverManager: свой probe, свой цикл опроса со своим темпом, снапшоты-поля, on_update-callback. Параллельно — ранний выход из `_drain` по детекции конца таблицы/prompt.

### 6.4 Жизненный цикл разделяемых ресурсов между entries

Находки #14, #31, #32: панель, ZvukClient и подписки живут дольше (или короче), чем нужно. Общий принцип: разделяемый ресурс существует, пока есть хотя бы один заинтересованный загруженный entry (refcounting), и явно закрывается при выгрузке последнего/остановке HA. Подписки панели резолвить по entry_id, а не по экземпляру координатора.

### 6.5 Границы модулей и «сетевая ошибка ≠ not_found» везде

- Находки #16, #44: закрепить публичные API (`client.server_action(...)`, `LyricsManager.supports_track`, `coordinator.stopping`) — тот же класс работ, что уже делали в 0.5.1; рассмотреть lint-правило/тест на импорт приватных символов между модулями.
- Находки #38, #39, #6: распространить инвариант №6 CLAUDE.md (не кэшировать сетевые сбои, retry после 401) на zvuk_client и camera — сейчас он соблюдён только в lyrics.
- Находки #45, #46, #47: механические DRY-вычистки (chrome-рендер кадра, `_dev()`, `_run_fetch`) — делать попутно при касании соответствующих файлов.

Дополнительно: парсерный слой (`_parsers.py`, `_tlv.py`) выиграл бы от свода «строгих» тестов на граничные случаи (скобки в строках — #1, числовой trackId — #25, обрезанные кадры — #26, 0x7b в префиксе — #27, нечисловой percent — #28) — все репродукции из этого ревью можно превратить в регрессионные тесты.

---

## 7. Приоритизированный план исправлений

### Волна 1 — безопасность и приватность (сделать немедленно, PATCH-релиз)

1. **#3** — расширить `TO_REDACT` (latitude/longitude/location_accuracy/network_ip/raw_state_json/raw). Однострочный фикс, устраняет утечку координат дома в публичные bug-report'ы.
2. **#2** — allowlist хостов + запрет не-https + лимит тела + LRU для `_color_cache` в `dominant_cover_color`.
3. **#41** — валидация панельного пути `sboom/play` (закрывается вместе с #17, но минимальный whitelist/isdigit можно внести сразу).

### Волна 2 — заметные пользователю баги (следующий PATCH/MINOR)

4. **#11** — нижняя граница font_size + try/except в `async_camera_image` (падение снапшота камеры).
5. **#9** — sentinel для `last_sec` (пустой MJPEG-стрим в BT).
6. **#7** — пересборка track из push GET_STATE (задержка 15 с на BT/радио).
7. **#6** — retry с `get_token(force=True)` после 401 (мёртвый поиск до рестарта HA).
8. **#4** — единый device_id (мёртвые device-триггеры manual-entry).
9. **#10** — корректный wrap длинных строк без пробелов (CJK-лирика).
10. **#13** — clamp дельты экстраполяции (откат позиции длинных треков).
11. **#8**, **#35**, **#36** — мелкие видимые фиксы (podcast-kind, обложка и позиция в панели).

### Волна 3 — конкурентность и жизненные циклы

12. **#5** — generation-счётчик для poll/push/optimistic (фундамент для #18).
13. **#14** — refcounting панели + удаление при unload.
14. **#32** — подписка панели по entry_id.
15. **#31** — `aclose()` на `EVENT_HOMEASSISTANT_STOP`.
16. **#15** — ранний выход `_drain` / вынос hw-опроса (вместе с #20).
17. **#33**, **#34** — сверка key в volatile-fetch; `to_thread` для `fallback_cover`.

### Волна 4 — устойчивость и гигиена ошибок

18. **#37**, **#22**, **#23** — обёртки HomeAssistantError в сервисах, `get_token` внутрь try, `ValueError` в except Lrclib.
19. **#38**, **#39** — не кэшировать сетевые сбои (цвет обложки, негативный кэш CDN).
20. **#1**, **#25**, **#26**, **#27**, **#28**, **#12** — hardening парсеров и границ + регрессионные тесты из репродукций ревью.
21. **#42** — честный `last_update_success` или чистка diagnostics.
22. **#24** (PLAUSIBLE) — снять live-сэмпл op=21 и решить вопрос кодировки RSSI.

### Волна 5 — архитектурный рефакторинг (MINOR, по мере касания кода)

23. **#18** — единый командный слой `async_execute` (после #5).
24. **#17** + **#19** — единый deeplink-источник, `_ha_helpers.py`.
25. **#16** — публичный `client.server_action(...)`, ликвидация доступа к приватным полям из `_deeplink.py`.
26. **#20** — выделение HwMonitor.
27. **#44**, **#43**, **#45**, **#46**, **#47** — инкапсуляция, мёртвый код, DRY-вычистки.
28. **#40** — TOFU-пиннинг сертификата (и/или фиксация трейдоффа в README).

---

*Отчёт составлен по результатам конкурентного двойного ревью с адверсариальной верификацией. Все находки CONFIRMED подтверждены чтением кода и/или репродукцией; севиритеты откалиброваны верификатором.*
