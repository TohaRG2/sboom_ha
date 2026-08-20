# research/ — black-box discovery SberBoom LAN-протокола

Всё что накопили **black-box behavioral observation** WSS-протокола `:20000`: чистой network-разведкой (мDNS/TLS/WS/proto-wire probing), поведенческим op-sweep'ом, action-fuzz'ом через state-diff и эмпирической валидацией на живой колонке в собственной сети. Без декомпиляции, дизассемблирования и обхода технических средств защиты (см. корневой `LEGAL.md`).

## Состояние research/ на сегодня

Слоёв уже несколько — читай в порядке приоритета:

### 📋 Протокольные документы (`.md`) — **точка входа для sboom_ha-разработчика**

- **[`PROTOCOL.md`](PROTOCOL.md)** — hop-by-hop лог восстановления протокола WSS `:20000` (envelope, pair-flow, op-sweep). Живой journal.
- **[`STAROS_PROTOBUF.md`](STAROS_PROTOBUF.md)** — справочник protobuf-схемы StarOS: op-коды, каталог сообщений `ru.sber.staros.protobuf`, feature-флаги устройств.
- **[`STATE_SCHEMA.md`](STATE_SCHEMA.md)** — схема JSON-payload'ов GET_STATE (`op12`), GET_META_DATA (`op10`), GET_PLAYING_QUEUE (`op17`).
- **[`RESUME_BY_AI_PROTOCOL.md`](RESUME_BY_AI_PROTOCOL.md)** — высокоуровневое описание TLV-транспорта для новых разработчиков.

### 📦 Реконструированные `.proto` справочники

- **`staros.proto`** — 154 message-типа `ru.sber.staros.protobuf.*`.
- **`staros_enums.proto`** — 90 enum'ов протокола (543 значения).

Собраны из наблюдаемых на wire сообщений; формально не компилируются в codegen, но пригодны как справочник имён полей для чтения серверных ответов.

### 🧪 `experiments/` — 21 живой эксперимент

Основной **journal разведки** (exp_01..exp_21) со своим [README](experiments/README.md) внутри. Каждый эксперимент — отдельный `.py` с фокусом (op-sweep, action-fuzz, push-subscribe, LED, playback-speed и т.д.).

### 🔧 Быстрые утилиты снятия состояния

- `capture_state.py` — снять GET_STATE / GET_META_DATA / GET_QUEUE в JSON (пополняет `state_dump/`).
- `probe_oneshot.py` — одноразовая проба конкретного op.
- `op_side_effects.py` — diff state до/после op.
- `lc_explore.py` — probe debug-CLI `:4242`.

### 💾 Готовые артефакты данных

- `protocol_map.json` / `protocol_map_extended.json` — итог pipeline discovery (envelope-роли, token-tag, op→action map).
- `state_dump/{all_keys,state,metadata,queue}.json` — эталонный снимок с живой R2, для сверки полей.
- `events.jsonl` — captured stream событий для timing/protocol drift анализа.

---

## Legacy black-box pipeline (01..07) — как это восстанавливалось изначально

Хронологически первый слой. Полезен если нужно **воспроизвести discovery-фазу** на новом устройстве или сравнить старую эмпирику с текущими знаниями. Для развития sboom_ha 99% времени актуальнее `experiments/`, `STAROS_PROTOBUF.md` и `.proto` — они уже отражают конечные ответы.

### Что знаем заранее (для чистой black-box сессии)

- Бинарный формат varint+length-delimited — **Google proto-wire encoding**, документирован: <https://protobuf.dev/programming-guides/encoding/>. Декодим через `protoc --decode_raw` или self-contained декодер `_shared.py`.
- WebSocket поверх TLS — RFC 6455.
- mDNS / Zeroconf — RFC 6762/6763.
- JSON — RFC 8259.

### Зависимости

```bash
pip install websockets zeroconf
# опционально — richer decode + type-learning + re-encode:
pip install bbpb        # https://github.com/nccgroup/blackboxprotobuf
```

### Полный pipeline (один-shot orchestrator)

`auto_discover.py` проходит все этапы за один запуск, промежуточные результаты в `protocol_map.json`.

```bash
python research/auto_discover.py
# или с готовым host:
python research/auto_discover.py --host <host> --port <port>
```

### Гранулярный workflow

| # | Файл | Что делает | Вход | Выход |
|---|------|-----------|------|-------|
| 1 | `01_discover.py` | mDNS broad-browse + опционально TCP-scan подсети | подсеть | список host:port + mDNS props |
| 2 | `02_probe.py` | TLS info + попытки HTTP `GET /` и WebSocket Upgrade | host:port | summary: TLS/WS/HTTP/raw |
| 3 | `03_capture.py` | Passive listen через generic proto-wire decoder | host:port | hex-дампы + TLV-дерево |
| 4 | `04_fuzz_envelope.py` | Систематический перебор top-level tag-номеров | host:port | таблица tag → behaviour |
| 5 | `05_pair_discovery.py` | Ищем «init»-op через перебор в body; оператор подтверждает физическую реакцию; извлекаем токен | host:port + envelope tag-роли + физический доступ | auth-токен |
| 6 | `06_action_fuzz.py` | Через action-op перебираем int-коды; diff JSON state → label семантики | host:port + token + envelope-роли + op-роли + играющий трек | action → семантика |
| 7 | `07_deep_fuzz.py` | Берёт `protocol_map.json` и расширяет: hole-sweep op-tag, subfield-fuzz, push-subscribe, TTS-scan | `protocol_map.json` | `protocol_map_extended.json` |

Shared-модули `_shared.py` (proto-wire decoder) и `_shared_bbpb.py` (опциональный bbpb backend) переиспользуются всеми нумерованными скриптами.

### Опциональная интеграция с `bbpb` (07_deep_fuzz)

`bbpb` (blackboxprotobuf от nccgroup) — решённая задача «декодинг proto-wire без `.proto`». `07_deep_fuzz.py` может использовать его вместо self-contained декодера:

```bash
python research/07_deep_fuzz.py --map protocol_map.json --use-bbpb \
    --typedef typedef.json --jsonl events.jsonl
```

Преимущества: type-learning между сессиями (`--typedef`), re-encode capability, streaming `events.jsonl`.
