# Protobuf-схема протокола StarOS (:20000 WSS)

Справочник сообщений локального протокола колонки, чтобы не пересобирать каждый
раз. Источник — имена protobuf-типов из StarOS-клиента (`box`), сверено с нашим
эмпирическим op-sweep (`op_map`) и `const.py`.

## Структура протокола

- Транспорт: **WebSocket++** (websocketpp), бинарные фреймы.
- Формат: **protobuf-lite**, envelope `ru.sber.staros.protobuf.StarMessage`.
- Envelope (по анализу трафика): `StarMessage { 2: msg_id, 5: request_data }`.
- Внутри `request_data` команда = **вложенное поле, номер которого = op** (наш
  op-код). На wire тег `(op<<3)|2` (wire_type 2 = LEN).
- `StarCommand` — oneof всех команд; `BaseCommand`, `StarAny`, `EventMessage`,
  `SystemMessage`, `ServerAction` — базовые обёртки.

### Как клиент раскладывает команду (подтверждённый механизм)

Обработка входящей команды в клиенте StarOS — **цепочка per-service
обработчиков** (chain of responsibility). Каждый сервис имеет метод-приёмник,
который переключается (`switch`) по **дискриминатору oneof-case** в разобранном
сообщении (смещение `+0x10` в объекте), обрабатывает «свои» кейсы и **делегирует
остальное** следующему обработчику. Неизвестный дискриминатор → error-путь.

Наблюдаемая структура (пример одного сервиса):
```
onCommand(msg):
  case = msg.oneof_case            # поле по смещению +0x10
  switch(case):
    case A: <handler A>            # часть кейсов сервис берёт на себя
    case B: <handler B>
    default: nextService(msg)      # fall-through в следующий обработчик
```

**Номера op ↔ имена:** в lite-сборке дескрипторов нет, поэтому точная привязка
номера к команде на wire берётся **эмпирически** (`op_side_effects.py`,
`op_map`). Внутренние `switch`-дискриминаторы обработчиков — это номера
**под-энумов** конкретных сервисов (навигация, системные поля и т.п.), их
равенство wire-op **не гарантировано** и требует отдельной проверки. Ниже —
эмпирика + полный словарь имён + найденные значения под-энумов.

## Известные op (реализовано в sboom_ha)

| op | Команда (наше имя) | Тип | Реализовано |
|---|---|---|---|
| 4 | PIN_CONNECT | handshake | ✅ config_flow |
| 10 | GET_META_DATA | read | ✅ (JSON: трек) |
| 12 | GET_STATE | read | ✅ (JSON: весь стейт) |
| 13 | FIND_REMOTE | write | ✅ button |
| 14 | SET_VOLUME | write | ✅ |
| 15 | SET_TRACK_POS | write | ✅ seek |
| 16 | MEDIA_COMMAND (16 суб-действий) | write | ✅ |
| 17 | GET_PLAYING_QUEUE | read | ⚠️ только trackId |
| 18 | HEARTBEAT (keepalive) | — | ✅ (молчит) |
| 19 | GET_PAIRED_BT | read | ✅ |
| 20 | BT_DEVICE_COMMAND | write | ✅ |
| 21 | GET_SCANNED_BT | read | ✅ |
| 22 | BT_DISCOVERABLE | write | ✅ |
| 23 | SET_PLAYBACK_SPEED | write | ✅ |

`op_map` (эмпирика, sweep 1–62): JSON-ответ дают только **10, 12, 17**; молчит
**18**; остальные 1–9, 11, 19–62 → минимальный ack (команды/эхо/диагностика).
op 5 = pair-cancel, 6 = acknowledge, 8 = focus voice_auth.

**op 18 = HEARTBEAT (keepalive)** — почему молчит, подтверждено с трёх сторон:
(1) в цепочке из 18 сервисных обработчиков дискриминатор 18 не встречается
вообще — команда не роутится в сервисы; (2) в клиенте есть отдельный
транспортный keepalive `BasicStarOSClient/BasicStarOSWsClient::pingPong(StarMessage)`
+ тип `ru.sber.staros.protobuf.PingPong` — heartbeat гасится на уровне
соединения; (3) `HEARTBEAT` присутствует значением в топ-левел командном enum
(рядом с `CLOSE_APP`/`GET_IHUB_TOKEN`/`RUN_APP`/`RUN_APP_DEEPLINK`/`SERVER_ACTION`/
`UPDATE_IP`). Т.е. 18 — служебный keepalive: сервер принимает и молча держит
сессию (не шлёт data-ответ), что и наблюдали в sweep.

Номера op 24–62 → команды из каталога ниже (SET_ALARM/SET_TIMER/DEVICE_SLEEP/…),
точная привязка номеров — TODO (эмпирический sweep + сверка эффектов).

Отдельно: пространство внутренних дискриминаторов клиента шире 62 (наблюдались
значения вплоть до ~228), т.е. команд заметно больше, чем покрыл sweep 1–62 —
но это номера **под-энумов сервисов**, не обязательно wire-op (см. выше).

## Каталог сообщений `ru.sber.staros.protobuf` (по категориям)

### Воспроизведение / медиа
`PlayerCommand`, `StopPlayers`, `MusicEvent`, `MusicAlarmInfo`, `Metadata`,
`Source`, `Wave`

### Громкость
`Volume`, `VolumeUp`, `VolumeDown`, `VolumeChanged`, `VolumeDriverApi`
(_SetVolumeSettings/_VolumeSettings), `SetDeviceVolume`, `SetMasterMono`

### Будильники / таймеры (WRITE — нам не хватает)
`SetAlarmClock`, `RemoveAlarmClock`, `SetSnoozeAlarmClock`, `RemoveClock`,
`PauseAlarm`, `ResumeAlarm`, `AlarmStopPlayingNow`, `AlarmPlaybackControl`,
`UpdateAlarmList`, `AlarmShareOurList`, `AlarmRemoved`, `AlarmsInfo`, `Alarm`
(_AlarmClock/_AlarmAction), `SetMusicAlarm`, `SetClock` ·
`SetTimer`, `RemoveTimer`, `PauseTimer`, `ResumeTimer`, `TimerNext`,
`Alarm_Timer` (_Kitchen/_Sleep/_PausedState/_TickingState)

### Питание / режимы
`DeviceSleep`, `DeviceWakeUp`, `DeviceNightMode`, `Reboot`, `PowerManagement`,
`SilenceModeRequest`, `SilenceModeChanged`, `DataReset`

### Мультирум / стереопара / дальний микрофон (farfield)
`MultiRoomCommand` (_CreatePlayer/_StartMultiRoom/_StartPlaying/_StopPlaying/
_SetVolume/_SetOverrun), `MultiRoomInfo`, `MultiRoomState`, `MultiRoomMessage`,
`MultiroomStateEvent`, `MultiRoomPlayerErrorOnSlave`, `StereoPairMessage`,
`StereoPairStateEvent`, `SlaveDeviceAquiredFocus`, `SlaveDeviceLostFocus` ·
`RemoteMicMessage`, `RemoteMicDevice`, `RemoteMicList`, `SetRemoteMic`,
`RemoteMicStateEvent`, `RemoteMicServiceStateEvent`, `RemoteMicReceiverStateEvent`,
`InitRemoteDeviceMessage`

### SberCast / группы устройств
`SberCastClientStatus`, `SberCastConnectionEstablished(Event)`,
`CastPinTokensInfo`, `DeviceGroupEvent`, `DeviceGroupChangedEvent`,
`GetDeviceGroupEvent`, `GetDeviceBackendGroup`, `SetDeviceBackendGroup`,
`GroupManagementMessage`, `SmartHomeParingEvent`

### Ассистент / голос
`AssistantApi` (_Text/_Voice), `AssistantApiResponse`, `AssistantSpeech`,
`CancelAssistantSpeech`, `AssistantState`, `AssistantInfo`, `AssistantError`,
`AssistantChangeCharacter`, `AssistantPlayerStart`, `ConversationEventInfo`,
`VoiceRecordingInterrupted`, `SaluteID`, `RabbitHoleRequest/Response`

### Контент / навигация / ТВ / IR
`RunSmartApp`, `CloseSmartApp`, `RunAppBlocked`, `ShowYoutube`, `Deeplink`,
`LauncherOpenItem`, `Search`, `Navigate`, `Navigated`, `NavigationCommand`,
`NavigationState`, `Home`, `Back`, `Close`, `PressKey`, `RemoteControlCommand`,
`TvPower`, `PayDialog` · `IRReceiverMessage`, `IRTransmitRequest`

### Smart apps
`SmartApp`, `SmartApps`, `SmartAppData`, `SmartAppError`, `SmartAppInfo`,
`SmartAppPush`, `SmartAppUIState`, `BaseSmartAppCommand`, `UpdateSmartApps`,
`PlatformApksApi`

### Bluetooth / BLE
`BluetoothMessage`, `BluetoothDevice`, `BluetoothStartDiscovery`,
`BluetoothToggleDiscovery`, `BluetoothDisconnect` · `BLEMessage` (+40 под-типов:
характеристики/discovery/advertising/connected-device), `StarBLEMessage`,
`BleSetup`, `BLESetupDevice`

### Биометрия / лица / пользователи
`BiometryCommand` (_Enroll/_Verify/_Confirm/_Delete), `BiometryResponse`,
`BiometricUser(s)`, `FaceDescriptor(s)`, `IdentifyResponse`, `UserAvatar`,
`UserSettings`, `UserTexts`, `AddressBook`, `Contact`

### Аутентификация / аккаунт
`AuthApi`, `AuthState`, `BackendAuthState`, `BackendAuthRegistered`,
`EsaAuthState`, `EsaAuthUpdate`, `ExpiredEsaToken`, `SuggestEsaAuth`,
`SuggestEsaRefresh`, `PartnerTokenRequest/Result`, `PlatformAuthApi`,
`UserAgreement(Status)`, `LoginStateSwitchStatus`

### Настройка / сеть / провижининг
`SetupApi`, `SetupStatus`, `SetupStatusExtended`, `SetupStorage`,
`WifiConnect`, `WifiConnectEvent`, `WifiList`, `WifiStatus`, `WifiInfo`,
`WifiModeTypeChange`, `EthernetConnect`, `EthernetInfo`, `ExternalIp`,
`SpeedTestResult`, `SetEnvironment`, `SwitchEnvironmentStatusEvent`,
`EnvironmentInfo`, `EnvironmentsInfo`

### Дисплей / устройство / система
`SetBacklightBrightness`, `ScreenState`, `ScreenSavers`, `DemoModeScreenSavers`,
`HdmiEdid`, `DeviceInfo`, `DeviceAssistantInfo`, `Capabilities`,
`CapabilitiesState`, `CapabilityCommand`, `GeneralState`, `UIState`,
`Location`, `TimeZone`, `TimeState`, `PtpInfo`

### Родительский контроль / лимиты
`ParentalControl`, `ParentalControlAPI`, `ParentalControlStart/Stop`,
`TimeLimit`, `TimeLimitConfiguration`, `DayTimeLimit`, `BlockedApps`
(Changed/Request), `DemoModeApi`

### Хранилища / заметки / уведомления
`CalendarStorage`, `StickyNotesStorage`, `LocationStorage`, `UserListStorage`,
`UiExtraDataStorage`, `SetupStorage`, `NotificationMessage`, `NotificationInfo`,
`NotificationResultMessage`, `PushNotification`, `SystemPush`

### Обновление / отчётность
`DownloadedUpdateInfo`, `Downloads`, `DownloadSettings`, `DownloadStatus`,
`UpdateStatus`, `UpdatePreviewTimeoutEnd`, `ForceConfigUpdateRequest` ·
`ReportEvent`, `ReportUploadEvent`, `MetricaData`, `MetricaEvent`,
`PerformanceAnalytics`, `BugReport`

### Прочее
`StarMessage`, `StarCommand`, `BaseCommand`, `StarAny`, `Empty`, `PingPong`,
`EventMessage`, `SystemMessage`, `ServerAction`, `AppSettings`,
`AppSettingsByFrontendEndpoint`, `AppState`, `Backup(s)`, `CommonConfig`,
`ContentVendor(s)`, `HomeSecuritySpotter`, `AdbStatus`, `GamepadSessionRequest`,
`GamepadSessionConnectionInfo`, `StartVideoGestureRecording`,
`StopVideoGestureRecording`, `SkipKeyboard`, `SkipStep`, `ConfirmResponse`

## Реконструкция .proto — метод и статус (spike ✅)

**Важная поправка:** ранее считали, что в lite-сборке имена полей вырезаны. Это
НЕ так — в клиенте присутствуют `full_name` полей в формате
`ru.sber.staros.protobuf.<Message>[.<Nested>].<field>` (напр.
`StarMessage.User.user_id`, `StarMessage.JsonWebToken.header`,
`StarMessage.Directive.payload`). Причина: клиент проверяет UTF-8 строковых
полей и передаёт туда имя поля для диагностики — так имена попадают в бинарь.

**Метод извлечения** (проверен на 3 сообщениях): у каждого сообщения есть
функция-сериализатор, из которой читается связка **номер поля + wire-тип +
тип + имя (для строк) + offset**:
- `WriteString(ctx, N, str, out)` → строковое поле номер N (+ имя из UTF-8-проверки);
- запись тега-байта `(N<<3)|2` → поле N, суб-сообщение (LEN);
- типизированные writer'ы (`WriteInt32`/`WriteBool`/`WriteEnum`/…) → скаляры.

**Что извлекается надёжно:** имена сообщений (272) и enum-значений; номера,
wire-типы, типы полей; имена строковых полей; offset'ы; oneof/repeated.
**Слабое место:** имена НЕ строковых полей (суб-сообщения/скаляры) UTF-8 не
проверяются → имя берётся из второго источника (JSON-ответы для read-полей,
эмпирика для write) или остаётся синтетическим.

**Пример (spike, реконструкция из сериализаторов):**
```proto
message JsonWebToken { string header=1; string payload=2; string signature=3; }
message User { string user_id=1; string access_token=2; string vps_user_id=3;
               SubMsg field4=4; SubMsg field5=5; }
message Volume { SubMsg field1=1; string payload=2; }
```

Вывод: полноценный `.proto` восстановим (с реальными именами для строковых
полей). Разумный объём — **таргетно** под фичи sboom_ha, не слепой дамп 272.

## Возможности устройства (device capabilities)

Прошивка `box` **единая для всех StarOS-устройств** (`sberboom`, `sberboom-mini`,
`sberbox`, `sberportal`, `satellite`…) — поэтому содержит все команды, а конкретная
модель поддерживает подмножество. Набор возможностей выражен **enum feature-флагов**
(значения отсортированы по алфавиту — protobuf enum):

```
CAN_OPEN_APPS · HAS_BLUETOOTH · HAS_YOUTUBE · HAS_SMOTRESHKA
HAS_SERVER_DEFINED_CEC · HAS_USER_CONTROLLED_CEC
HOME_SECURITY_FEATURE_ENABLED · IS_EDU_MODE
MUSIC_BLE_DEEPLINK_FEATURE_ENABLED · MUSIC_ENABLE_FLAC · MUSIC_SHOW_VISUALIZER
UNDEFINED_FLAG (+ Capabilities.hasScreen)
```

**Идентичность модели:** `DeviceInfo` = { brand_name, device_id,
device_serial_number, display_name, product, **surface**, vendor, version }.
`surface`/`product` = форм-фактор (напр. `sberboom-mini`) → по нему клиент решает,
что показывать. Модель нашего устройства — `sberboom-r2`.

**Где живут флаги:** backend шлёт JSON-конфиги — `CommonConfig.config`
(`Configuration::commonConfig() → Json::Value`) и `AppSettings.app_settings_json`;
device их сохраняет. TV-специфика (CEC/IR/экран/подсветка) — в `Capabilities` /
`CapabilitiesState` (+ `CapabilityCommand`: CAP_TV_ON/OFF/TOGGLE, CAP_VOLUME_*).

**Как использовать в sboom_ha:** вместо угадывания op — читать `DeviceInfo`
(product/surface → профиль модели) и искать флаги в живом `GET_STATE` (op 12,
уже принимаем JSON). Проверить эмпирически: снять GET_STATE с колонки и найти
ключи product/surface/flags/capabilities.

**Каталог enum'ов протокола** (в .rodata packed-таблицами): типы директив
(SHOW_YOUTUBE/STOP_PLAYERS/VOLUME_UP…), источники команд (VOICE/TEXT/SBER_CAST/
SERVER_ACTION…), аудио-фокус (GAIN/LOSS…), аудио-потоки (STREAM_ALARM/ASSISTANT/
BLUETOOTH/MUSIC), ServerAction (ADD_USER/REBOOT_DEVICE/SET_MUTE/SET_SCREEN/
SET_VOLUME/UNLINK_DEVICE…), статусы сети/обновлений/приложений.

## SberCast под-протокол (`sbercast.protobuf`, отдельный сокет)

Пары `_Request/_Response`:
- `GetState`, `GetMetaData`, `GetWifiList`, `PinConnect`, `ConfirmPinConnect`,
  `CancelPinConnect`, `ConnectWifi`, `GamepadSession`, `PingPong`,
  `SmartAppState`, `VoiceTransport`
- `SberCastMessage`, `SberCastResponseMessage`, `CastRequestData`,
  `CastDirectiveData`, `CastStarCommandData`
- `SberCastBLERequest/Response` (_SSLValidate) — BLE-канал каста
- **`IrdRequest`/`IrdResponse`** — first-setup/провижининг (EsaStatus, WifiConnect,
  SetMeUp, StartBleEasySetup, GetPin, GetDeviceInfo, ShowEsaCode…)

## Найденные значения под-энумов сервисов

Из анализа клиента StarOS удалось прочитать имена значений для нескольких
сервисных под-энумов (строковые метки, которыми клиент маркирует ветки
обработчиков). **Это НЕ wire-op**, а внутренние дискриминаторы соответствующих
сервисов; полезны как словарь и для сверки эффектов:

| Значение | Имя | Сервис / смысл |
|---|---|---|
| 10 | `CLOSE_APP` | навигация/UI |
| 101 | `HOME` | навигация/UI |
| 102 | `BACK` | навигация/UI |
| 117 | `START_VIDEO_GESTURE_RECORDING` | видео-жесты |
| 118 | `STOP_VIDEO_GESTURE_RECORDING` | видео-жесты |
| 0 | `TARGET_STAR_VERSION` | системные поля |
| 219/228 | `FORCE_SHOW_UPDATE_STEP`, `BACKEND_SESSION_ID` | системные/сессия |

Совпадают с типами каталога (`Home`, `Back`, `Close`, `Start/StopVideoGesture
Recording`) — подтверждает вокабуляр, но не даёт wire-нумерацию.

## Как достать точные номера op (TODO)

1. **Эмпирика**: `research/op_side_effects.py` — шлём op N → diff GET_STATE →
   сопоставляем эффект с именем команды из каталога. Расширить sweep за 62.
2. **Сверка под-энумов**: сопоставить наблюдаемые эффекты с именами значений
   выше (напр. послать команду навигации и увидеть `HOME`/`BACK`).

Приоритет для sboom_ha: `SetAlarmClock`/`SetTimer` (write будильников/таймеров),
`AssistantApi_Text`/`_Voice` (инъекция команд Салюту), `DeviceSleep`/`WakeUp`/
`Reboot`, `RemoteMic*` (farfield), `IRTransmitRequest`.
