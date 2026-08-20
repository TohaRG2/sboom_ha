"""Координатор: один WS-клиент на колонку, push state-updates."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ._parsers import track_from_state
from .api import BluetoothDevice, SberSpeakerClient, SpeakerState, TrackInfo
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_NAME,
    CONF_HOST,
    CONF_PIN_ACCESS_TOKEN,
    CONF_PORT,
    DEFAULT_AVAILABILITY_THRESHOLD,
    DEFAULT_KARAOKE_FILL,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_LYRICS_ENABLED,
    DEFAULT_LYRICS_NETEASE,
    DEFAULT_LYRICS_OFFSET,
    DEFAULT_PORT,
    DEFAULT_VOLUME_POLL_INTERVAL,
    DOMAIN,
    ENVELOPE_FIELD_REQUEST_DATA,
    OP_GET_META_DATA,
    OP_GET_STATE,
    OPT_AVAILABILITY_THRESHOLD,
    OPT_KARAOKE_FILL,
    OPT_KEEPALIVE_INTERVAL,
    OPT_LYRICS_ENABLED,
    OPT_LYRICS_NETEASE,
    OPT_LYRICS_OFFSET,
    OPT_VOLUME_POLL_INTERVAL,
    POLL_FAILURES_BEFORE_RECONNECT,
    RECONNECT_BACKOFF_SEC,
    STABLE_SESSION_SEC,
)
from .cover_manager import CoverManager
from .helpers import sber_device_id
from .hw_monitor import HwMonitor
from .lyrics_client import Lyrics
from .lyrics_manager import LyricsManager

# Event types для HA event bus.
EVENT_TRACK_CHANGED = "sboom_track_changed"
EVENT_PLAYBACK_CHANGED = "sboom_playback_changed"
EVENT_VOLUME_CHANGED = "sboom_volume_changed"
EVENT_CONNECTION_CHANGED = "sboom_connection_changed"

# Issue: колонка недоступна больше N секунд → создаём info-issue в Repairs.
UNREACHABLE_ISSUE_THRESHOLD_SEC = 300  # 5 минут

_LOGGER = logging.getLogger(__name__)

# Единый командный слой (аудит #18): action → (метод клиента, тип value).
# Используется media_player/switch/number/select и ws-командами панели.
COMMAND_SPECS: dict[str, tuple[str, str | None]] = {
    "play": ("media_play", None),
    "pause": ("media_pause", None),
    "next": ("media_next", None),
    "prev": ("media_prev", None),
    "previous": ("media_prev", None),
    "mute": ("media_mute", None),
    "unmute": ("media_unmute", None),
    "like": ("media_like", None),
    "remove_like": ("media_remove_like", None),
    "dislike": ("media_dislike", None),
    "remove_dislike": ("media_remove_dislike", None),
    "find_remote": ("find_remote", None),
    "volume": ("set_volume", "int"),
    "seek": ("seek_to", "int"),
    "shuffle": ("media_shuffle", "bool"),
    "repeat": ("media_repeat", "str"),
    "playback_speed": ("set_playback_speed", "float"),
}

# Volume/mute НЕ приходят push'ем — после команды нужен debounced refresh
# для подтверждения. Остальное колонка подтверждает push-событием.
_REFRESH_ACTIONS = frozenset({"volume", "mute", "unmute"})


def _coerce_value(kind: str, value: Any) -> Any:
    """Привести value из JSON/сервиса к типу, ожидаемому методом клиента."""
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "bool":
        return bool(value)
    return str(value)


class SboomCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Один coordinator на одну колонку.

    Хранит:
      - latest SpeakerState (volume, muted)
      - latest TrackInfo
    Поднимает long-lived WS-сессию, шлёт KeepAlive, обрабатывает unsolicited
    state-update'ы от колонки и форсит refresh подписчиков.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Читаем опции, fallback на дефолты — для существующих entries без options.
        opts = entry.options
        self._volume_poll_interval = int(
            opts.get(OPT_VOLUME_POLL_INTERVAL, DEFAULT_VOLUME_POLL_INTERVAL)
        )
        self._keepalive_interval = int(
            opts.get(OPT_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL)
        )
        self._availability_threshold = int(
            opts.get(OPT_AVAILABILITY_THRESHOLD, DEFAULT_AVAILABILITY_THRESHOLD)
        )
        self._lyrics_enabled = bool(opts.get(OPT_LYRICS_ENABLED, DEFAULT_LYRICS_ENABLED))
        self._lyrics_netease = bool(opts.get(OPT_LYRICS_NETEASE, DEFAULT_LYRICS_NETEASE))
        # Посимвольная караоке-закраска строки (по умолчанию off — статичный текст).
        self.karaoke_fill = bool(opts.get(OPT_KARAOKE_FILL, DEFAULT_KARAOKE_FILL))
        # Пользовательский сдвиг лирики (сек): + = строки раньше, − = позже.
        self.lyrics_offset = float(opts.get(OPT_LYRICS_OFFSET, DEFAULT_LYRICS_OFFSET))

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}:{entry.data.get(CONF_HOST)}",
            # State-pushes (volume/mute) от колонки НЕ приходят — приходит только
            # metadata. Поллим volume по сконфигурированному интервалу.
            update_interval=timedelta(seconds=self._volume_poll_interval),
        )
        self.entry = entry
        self.client = SberSpeakerClient(
            host=entry.data[CONF_HOST],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            client_id=entry.data[CONF_CLIENT_ID],
            client_name=entry.data.get(CONF_CLIENT_NAME, "Home Assistant"),
            pin_access_token=entry.data[CONF_PIN_ACCESS_TOKEN],
            on_event=self._handle_event,
        )

        self.state: SpeakerState | None = None
        self.track: TrackInfo | None = None
        self.paired_bt: list[BluetoothDevice] = []  # спаренные BT-устройства (op=19)
        self.connected: bool = False  # доступность колонки (для entity.available)
        self._unreachable_since: float | None = None  # monotonic timestamp
        self._supervisor_task: asyncio.Task | None = None
        self._stopping = False
        # Подписчики на остановку координатора (unload/reload entry) —
        # напр. push-подписка панели, которой нужно терминальное событие,
        # чтобы переподписаться на новый инстанс (аудит #32).
        self._stop_listeners: list[Callable[[], None]] = []

        # Аппаратная подсистема (libiio-датчики + Zigbee/Matter CLI) вынесена
        # в HwMonitor (SRP, аудит #20) — есть только у части моделей (R2).
        # Сенсоры читают её через coordinator.hw.*; поля-делегаты ниже — для
        # обратной совместимости старого доступа coordinator.iio_cap и т.п.
        self.hw = HwMonitor(entry.data[CONF_HOST])
        # Подряд идущие полностью неудачные poll-циклы при живом на вид сокете —
        # страховка от half-open, который не поймал транспортный WS ping.
        self._poll_failures = 0
        # Generation-счётчик мутаций state/track из push/optimistic-путей.
        # Poll снимает снапшот перед await и отбрасывает свой ответ, если
        # счётчик изменился: ответ сформирован ДО более свежих данных
        # (last-write-wins гонка — аудит #5).
        self._mutations = 0

        self._http = async_get_clientsession(hass)
        # Жизненный цикл текстов песен — в отдельном менеджере (SRP).
        self.lyrics = LyricsManager(
            hass, entry, self._http,
            enabled=self._lyrics_enabled,
            netease_fallback=self._lyrics_netease,
            on_update=self.async_update_listeners,
        )
        # Обложка для BT/радио (нет каталожного id) — по title+artist из iTunes/Deezer.
        self.cover = CoverManager(
            hass, entry, self._http, on_update=self.async_update_listeners,
        )

    @property
    def http_session(self):
        """Shared aiohttp session — для подплатформ (camera/sensor)."""
        return self._http

    @property
    def device_state(self):
        """Подсистемы устройства из последнего GET_STATE, либо None.

        Единственный источник для sensor/binary_sensor/_entity_base —
        вместо трёх локальных копий `_dev()` (аудит #46).
        """
        return self.state.device if self.state else None

    @property
    def stopping(self) -> bool:
        """True во время unload/reload entry (публичный доступ для diagnostics)."""
        return self._stopping

    # ─────────────────────── lifecycle ───────────────────────

    async def async_start(self) -> None:
        """Поднять связь с колонкой.

        Первый connect выполняется СИНХРОННО: если колонка сейчас недоступна,
        бросаем `ConfigEntryNotReady` — HA отложит и повторит setup, вместо
        того чтобы поднять интеграцию в заведомо мёртвом состоянии. Дальнейшие
        реконнекты при обрывах ведёт фоновый supervisor.
        """
        self._stopping = False
        await self.lyrics.async_load()
        try:
            await self._connect_and_sync()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.client.close()
            raise ConfigEntryNotReady(
                f"колонка {self.client.host} недоступна: {exc}"
            ) from exc
        # Определяем аппаратные возможности колонки (libiio, Zigbee-CLI).
        # Закрытый порт → мгновенный refused, не тормозит setup; probe'ы
        # параллельны. Сбой любого — просто «нет capability».
        await self._probe_hw_capabilities()

        self._supervisor_task = self.hass.async_create_background_task(
            self._supervisor(), name=f"{DOMAIN}-supervisor"
        )

    async def _probe_hw_capabilities(self) -> None:
        """Один раз при старте: определить аппаратные capability модели."""
        await self.hw.async_probe()

    # ─────────────── hw-делегаты (обратная совместимость) ───────────────
    # Сенсоры и тесты читают/пишут coordinator.iio_cap и т.п.; фактическое
    # состояние живёт в HwMonitor (аудит #20).

    @property
    def iio_cap(self):
        return self.hw.iio_cap

    @iio_cap.setter
    def iio_cap(self, value) -> None:
        self.hw.iio_cap = value

    @property
    def iio_reading(self):
        return self.hw.iio_reading

    @iio_reading.setter
    def iio_reading(self, value) -> None:
        self.hw.iio_reading = value

    @property
    def has_zigbee_cli(self) -> bool:
        return self.hw.has_zigbee_cli

    @has_zigbee_cli.setter
    def has_zigbee_cli(self, value: bool) -> None:
        self.hw.has_zigbee_cli = value

    @property
    def has_matter_cli(self) -> bool:
        return self.hw.has_matter_cli

    @has_matter_cli.setter
    def has_matter_cli(self, value: bool) -> None:
        self.hw.has_matter_cli = value

    @property
    def zigbee_devices(self):
        return self.hw.zigbee_devices

    @zigbee_devices.setter
    def zigbee_devices(self, value) -> None:
        self.hw.zigbee_devices = value

    @property
    def matter_devices(self):
        return self.hw.matter_devices

    @matter_devices.setter
    def matter_devices(self, value) -> None:
        self.hw.matter_devices = value

    async def _connect_and_sync(self) -> None:
        """Один connect + listener + стартовый sync. Бросает исключение при неудаче.

        ВАЖНО: get_metadata (внутри _refresh_state_and_track) активирует
        push-subscribe stream — после него устройство пушит unsolicited
        updates на каждое изменение track/play/pause/volume.
        """
        await self.client.connect()
        self.client.start_listening()
        # Строгая проверка: соединение считается рабочим только после первого
        # успешного запроса. Иначе колонка, принимающая WS, но не отвечающая
        # (например, отозванный токен), выглядела бы «подключённой».
        self.state = self._merge_state(await self.client.get_state())
        self._poll_failures = 0
        self._set_connected(True)
        _LOGGER.debug("connected to %s", self.client.host)
        await self._refresh_state_and_track()

    def async_add_stop_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Подписаться на остановку координатора. Возвращает unsubscribe."""
        self._stop_listeners.append(listener)

        def _remove() -> None:
            if listener in self._stop_listeners:
                self._stop_listeners.remove(listener)

        return _remove

    async def async_stop(self) -> None:
        self._stopping = True
        for listener in list(self._stop_listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("stop-listener координатора упал")
        self._stop_listeners.clear()
        self._set_connected(False)
        if self._supervisor_task:
            self._supervisor_task.cancel()
            self._supervisor_task = None
        await self.client.close()
        # Флашим отложенный (debounced) save лирики сейчас: после unload
        # запланированный async_delay_save писал бы в Store мёртвого entry.
        await self.lyrics.async_flush()

    # ─────────────────────── connection supervisor ───────────────────────

    async def _supervisor(self) -> None:
        attempt = 0
        while not self._stopping:
            session_started: float | None = None
            try:
                # Первый заход: connect уже выполнен синхронно в async_start,
                # соединение живое — пропускаем. Последующие заходы (после
                # обрыва) — реконнектимся.
                if self.client.disconnected.is_set():
                    await self._connect_and_sync()
                session_started = time.monotonic()

                # Транспорт держит WS ping/pong (см. api.connect); op 18
                # keep-alive шлём дополнительно как application-level страховку
                # (эффект не изолирован — см. OP_KEEP_ALIVE в const.py).
                # Track-changes приходят push-events через _handle_event.
                while not self._stopping:
                    # Ждём либо истечения keepalive-интервала, либо сигнала
                    # обрыва от listen-loop — что наступит раньше. Так разрыв
                    # ловится мгновенно, а не через ~keepalive_interval секунд.
                    try:
                        await asyncio.wait_for(
                            self.client.disconnected.wait(),
                            timeout=self._keepalive_interval,
                        )
                        _LOGGER.debug("WS-обрыв замечен listen-loop'ом — reconnect")
                        break
                    except TimeoutError:
                        pass  # keepalive-интервал прошёл штатно
                    try:
                        await self.client.keep_alive()
                    except Exception as exc:
                        _LOGGER.debug("keepalive failed (%s), dropping connection", exc)
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.debug("WS error: %s", exc)
            finally:
                await self.client.close()

            if self._stopping:
                break

            # Backoff-серия сбрасывается только если сессия прожила достаточно
            # долго. Flapping (connect проходит, но сразу рвётся) продолжает
            # эскалацию и в итоге честно помечает колонку недоступной.
            if (
                session_started is not None
                and time.monotonic() - session_started >= STABLE_SESSION_SEC
            ):
                attempt = 0

            attempt += 1
            # после N подряд неудач помечаем колонку недоступной
            # (лог — один раз, при переходе connected → False внутри _set_connected)
            if attempt >= self._availability_threshold:
                self._set_connected(False)
                self._maybe_create_unreachable_issue()

            backoff = RECONNECT_BACKOFF_SEC[min(attempt - 1, len(RECONNECT_BACKOFF_SEC) - 1)]
            backoff = backoff + random.uniform(0, backoff * 0.3)
            _LOGGER.debug("reconnect in %.1fs (attempt %d)", backoff, attempt)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise

    # ─────────────────────── lyrics (фасад над LyricsManager) ───────────────────────

    def current_lyrics(self) -> Lyrics | None:
        """Lyrics для активного трека (или None если ещё не загружено / не нашлось)."""
        return self.lyrics.current_for(self.track)

    def current_cover(self) -> str | None:
        """URL обложки для BT/радио (по title+artist), либо None. Каталог — cover_url()."""
        return self.cover.current_for(self.track)

    def _maybe_fetch_lyrics(self) -> None:
        self.lyrics.maybe_fetch(self.track)
        self.cover.maybe_fetch(self.track)

    # ─────────────────────── единый командный слой ───────────────────────

    async def async_execute(self, action: str, value: Any = None) -> None:
        """Выполнить команду к колонке: вызов клиента + optimistic-патч + refresh.

        Единственное место с политикой подтверждения команд (аудит #18):
        entity-платформы и ws-команды панели — тонкие адаптеры. ValueError —
        неизвестный action или отсутствующее/некорректное value.
        """
        spec = COMMAND_SPECS.get(action)
        if spec is None:
            raise ValueError(f"unknown action: {action}")
        method_name, value_kind = spec
        if value_kind is not None:
            if value is None:
                raise ValueError(f"{action} requires value")
            value = _coerce_value(value_kind, value)
        method = getattr(self.client, method_name)
        if value_kind is None:
            await method()
        else:
            await method(value)
        self._apply_optimistic_for(action, value)
        if action in _REFRESH_ACTIONS:
            await self.async_request_refresh()

    def _apply_optimistic_for(self, action: str, value: Any) -> None:
        """Optimistic-патч состояния после успешной команды.

        next/prev/seek/find_remote — без патча: трек/позиция придут push'ем.
        """
        if action == "play":
            self.apply_optimistic_track(playing=True)
        elif action == "pause":
            self.apply_optimistic_track(playing=False)
        elif action == "like":
            self.apply_optimistic_track(liked=True)
        elif action in ("remove_like", "dislike"):
            self.apply_optimistic_track(liked=False)
        elif action == "mute":
            self.apply_optimistic_state(muted=True)
        elif action == "unmute":
            self.apply_optimistic_state(muted=False)
        elif action == "volume" and value is not None:
            self.apply_optimistic_state(volume_percent=int(value))
        elif action == "shuffle" and value is not None:
            self.apply_optimistic_track(shuffle=bool(value))
        elif action == "repeat" and value is not None:
            self.apply_optimistic_track(repeat=str(value))
        elif action == "playback_speed" and value is not None:
            self.apply_optimistic_track(playback_speed=float(value))

    # ─────────────────────── optimistic updates ───────────────────────

    def _bump_mutations(self) -> None:
        """Отметить push/optimistic-мутацию state/track (см. `_mutations`)."""
        self._mutations += 1

    def apply_optimistic_state(self, **changes: Any) -> None:
        """Локально патчит SpeakerState сразу после успешной команды.

        Volume/mute НЕ приходят push'ем, а poll идёт раз в N секунд — без
        optimistic-патча повторные команды (volume_up × 3) читали бы
        устаревшее значение и не аккумулировались. Следующий poll подтвердит.
        """
        if self.state is None:
            return
        self.state = replace(self.state, **changes)
        self._bump_mutations()
        self.async_update_listeners()

    def apply_optimistic_track(self, **changes: Any) -> None:
        """Локально патчит TrackInfo после команды (play/pause/shuffle/repeat)."""
        if self.track is None:
            return
        self.track = replace(self.track, **changes)
        self._bump_mutations()
        self.async_update_listeners()

    # ─────────────────────── data handlers ───────────────────────

    def _merge_state(self, new: SpeakerState | None) -> SpeakerState | None:
        """Домердживает недостающие поля нового state из прежнего.

        Частичный или битый payload (parse_state вернул None либо state без
        volume-блока) не должен обнулять громкость/mute/device-сенсоры в UI.
        """
        if new is None:
            return self.state
        old = self.state
        if old is not None:
            if new.volume_percent is None:
                new.volume_percent = old.volume_percent
            if new.muted is None:
                new.muted = old.muted
            if new.device is None:
                new.device = old.device
        return new

    def _stamp_track(self, track: TrackInfo | None) -> TrackInfo | None:
        """Помечает трек временем получения на стороне HA.

        monotonic — база экстраполяции позиции (часы колонки могут расходиться
        с часами HA), unix-время — для media_position_updated_at.

        ВАЖНО: если payload несёт ТОТ ЖЕ снапшот позиции, что уже есть
        (track_id + position_ts_ms + position_sec не изменились — т.е. на
        колонке не было нового события), база экстраполяции ПЕРЕНОСИТСЯ со
        старого трека. Poll-ответ get_metadata возвращает позицию на момент
        последнего события, а не текущую: свежий штамп на несвежей позиции
        откатывал бы воспроизведение к stale-значению на каждый poll
        (наблюдалось как «таймлайн сбрасывается на 0 каждые 15 секунд»).
        """
        if track is None:
            return None
        prev = self.track
        if (
            prev is not None
            and prev.received_monotonic is not None
            and prev.track_id == track.track_id
            and prev.position_ts_ms == track.position_ts_ms
            and prev.position_sec == track.position_sec
        ):
            track.received_monotonic = prev.received_monotonic
            track.received_ts = prev.received_ts
        else:
            track.received_monotonic = time.monotonic()
            track.received_ts = time.time()
        return track

    def _maybe_update_track_from_state(self) -> None:
        """Пересобрать now-playing из свежего GET_STATE (push-путь).

        Смена песни по Bluetooth/радио приходит push'ем GET_STATE (metadata-push
        для них не бывает — нет trackId); без пересборки title/artist ждали бы
        следующего volume-poll'а. Каталожный трек не затирается: его обновляет
        OP_GET_META_DATA, state-производный принимается только если играет
        (реальное переключение источника на BT/радио).
        """
        derived = self._track_from_current_state()
        if derived is None or derived.track_id:
            return
        if self.track is None or not self.track.track_id or derived.playing:
            self.track = self._stamp_track(derived)
            self._maybe_fetch_lyrics()

    def _track_from_current_state(self) -> TrackInfo | None:
        """Now-playing из последнего GET_STATE (радио/Bluetooth — без trackId)."""
        if not self.state or not self.state.raw_state_json:
            return None
        try:
            data = json.loads(self.state.raw_state_json)
        except (json.JSONDecodeError, ValueError):
            return None
        return track_from_state(data)

    async def _refresh_state_and_track(self, *, notify: bool = True) -> None:
        prev_track = self.track
        prev_state = self.state
        poll_ok = False
        gen = self._mutations
        try:
            new_state = await self.client.get_state()
            poll_ok = True
            if self._mutations == gen:
                self.state = self._merge_state(new_state)
                _LOGGER.debug("get_state -> volume=%s muted=%s",
                              self.state.volume_percent if self.state else "?",
                              self.state.muted if self.state else "?")
            else:
                # Во время await пришёл push/optimistic — ответ сформирован
                # до него и уже стейл. Следующий poll подтвердит.
                _LOGGER.debug("get_state отброшен: state изменился во время await")
        except Exception as exc:
            # Обрыв в процессе poll'а — штатно, супервизор реконнектит.
            _LOGGER.debug("get_state failed: %s", exc)
        gen = self._mutations
        try:
            new_track = await self.client.get_metadata()
            poll_ok = True
            if self._mutations != gen:
                _LOGGER.debug("get_metadata отброшен: track изменился во время await")
            else:
                self.track = self._stamp_track(new_track)
                self._maybe_fetch_lyrics()
                if self.track:
                    _LOGGER.debug(
                        "get_metadata -> title=%r artists=%s album=%r track_id=%s "
                        "release_id=%s playing=%s pos=%s/%s prov=%s",
                        self.track.title, self.track.artists, self.track.album,
                        self.track.track_id, self.track.release_id,
                        self.track.playing, self.track.position_sec,
                        self.track.duration_sec, self.track.provider,
                    )
                else:
                    # get_metadata пуст (радио/Bluetooth — нет trackId). Now-playing
                    # для них живёт в GET_STATE (music/bluetooth_media_control app).
                    self.track = self._stamp_track(self._track_from_current_state())
                    if self.track:
                        self._maybe_fetch_lyrics()
                        _LOGGER.debug(
                            "track_from_state -> title=%r station=%r source=%s playing=%s",
                            self.track.title, self.track.station_name,
                            self.track.media_source, self.track.playing,
                        )
                    else:
                        _LOGGER.debug("get_metadata и GET_STATE без now-playing")
        except Exception as exc:
            _LOGGER.debug("get_metadata failed: %s", exc)
        try:
            self.paired_bt = await self.client.get_paired_bt_devices()
        except Exception as exc:
            _LOGGER.debug("get_paired_bt_devices failed: %s", exc)

        # Аппаратные датчики / Zigbee-инвентарь (только если модель умеет).
        if self.hw.any_capability:
            try:
                await self.hw.async_poll()
            except Exception as exc:
                _LOGGER.debug("hw poll failed: %s", exc)

        # Страховка от half-open: сокет выглядит живым, но все запросы
        # таймаутят. N подряд полностью неудачных циклов → принудительный
        # close(), супервизор поднимет соединение заново.
        if poll_ok:
            self._poll_failures = 0
        else:
            self._poll_failures += 1
            if self._poll_failures >= POLL_FAILURES_BEFORE_RECONNECT:
                _LOGGER.debug(
                    "%d подряд неудачных poll-циклов — принудительный reconnect",
                    self._poll_failures,
                )
                self._poll_failures = 0
                await self.client.close()

        self._fire_change_events(prev_track, prev_state)
        # notify=False, когда вызывает _async_update_data: штатный цикл
        # координатора сам уведомит listeners после return. Вызов
        # async_set_updated_data изнутри цикла обновления давал бы двойную
        # перерисовку всех entities на каждый poll.
        if notify:
            self.async_set_updated_data({"state": self.state, "track": self.track})

    async def _handle_event(self, raw: bytes, parsed: dict[int, Any]) -> None:
        """Колонка отправила unsolicited / push message — обновляем state/track."""
        req_data = parsed.get(ENVELOPE_FIELD_REQUEST_DATA)
        if not isinstance(req_data, dict):
            return
        prev_track = self.track
        prev_state = self.state
        changed = False
        metadata_updated = False
        if OP_GET_META_DATA in req_data:    # MetaData update
            try:
                new_track = self._stamp_track(self.client.parse_track(raw))
                if new_track is not None:
                    self.track = new_track
                    self._maybe_fetch_lyrics()
                    changed = True
                    metadata_updated = True
            except Exception:  # pragma: no cover
                _LOGGER.exception("metadata push parse failed")
        if OP_GET_STATE in req_data:    # State update
            try:
                new_state = self.client.parse_state(raw)
                if new_state is not None:
                    # Диагностика канала доставки громкости: research-доки
                    # противоречат друг другу (PROTOCOL.md: «volume changes
                    # триггерят push»; RESUME: «volume НЕ приходит push'ем»).
                    # Если в логах появится volume=<число> — комментарии и
                    # интервал поллинга можно пересматривать.
                    _LOGGER.debug(
                        "state-push получен: volume=%s muted=%s (маркеры=%s)",
                        new_state.volume_percent, new_state.muted,
                        sorted(k for k in req_data if isinstance(k, int)),
                    )
                    self.state = self._merge_state(new_state)
                    changed = True
                    if not metadata_updated:
                        self._maybe_update_track_from_state()
            except Exception:
                _LOGGER.exception("state push parse failed")
        if changed:
            self._bump_mutations()
            self._fire_change_events(prev_track, prev_state)
            # Прямое обновление + update_listeners вместо async_set_updated_data:
            # последний отменяет pending request_refresh (подтверждение команд)
            # и переносит volume-poll на +interval при каждом push — при
            # активном воспроизведении громкость старела бы неограниченно.
            self.data = {"state": self.state, "track": self.track}
            self.async_update_listeners()

    # ─────────────────────── event bus ───────────────────────

    def _set_connected(self, connected: bool) -> None:
        """Обновить флаг доступности и стрельнуть событием при изменении.

        Логи — только на переходах (IQS log-when-unavailable): одно WARNING
        при потере связи, одно INFO при восстановлении. Отдельные reconnect-
        попытки логируются на DEBUG в супервизоре.
        """
        if self.connected == connected:
            return
        self.connected = connected
        if connected:
            _LOGGER.info("связь с колонкой %s установлена", self.client.host)
            self._unreachable_since = None
            self._clear_unreachable_issue()
        else:
            if not self._stopping:
                _LOGGER.warning(
                    "колонка %s недоступна — реконнект продолжается в фоне",
                    self.client.host,
                )
            self._unreachable_since = time.monotonic()
        if self._stopping:
            # Штатный unload/reload — не событие для автоматизаций
            # и перерисовывать уже нечего.
            return
        self.hass.bus.async_fire(EVENT_CONNECTION_CHANGED, {
            **self._event_payload_base(),
            "connected": connected,
        })
        # форсим entity-перерисовку (чтобы available подхватился сразу)
        self.async_update_listeners()

    def _maybe_create_unreachable_issue(self) -> None:
        """Если колонка недоступна больше UNREACHABLE_ISSUE_THRESHOLD_SEC — issue в Repairs."""
        if self.connected or self._unreachable_since is None:
            return
        elapsed = time.monotonic() - self._unreachable_since
        if elapsed < UNREACHABLE_ISSUE_THRESHOLD_SEC:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"unreachable_{self.entry.entry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="speaker_unreachable",
            translation_placeholders={
                "name": self.entry.data.get("device_name") or self.entry.data.get(CONF_HOST, "speaker"),
                "minutes": str(int(elapsed // 60)),
            },
            # entry_id нужен fix-flow (repairs.py), чтобы обновить host у entry.
            data={"entry_id": self.entry.entry_id},
        )

    def _clear_unreachable_issue(self) -> None:
        ir.async_delete_issue(
            self.hass, DOMAIN, f"unreachable_{self.entry.entry_id}"
        )

    def _event_payload_base(self) -> dict[str, Any]:
        """Общая часть полезной нагрузки события: device-контекст.

        device_id — через общий sber_device_id (fallback на host): тот же
        идентификатор, что в identifiers устройства и фильтрах device-триггеров.
        """
        return {
            "entry_id": self.entry.entry_id,
            "device_id": sber_device_id(self.entry),
            "host": self.entry.data.get(CONF_HOST),
        }

    def _fire_change_events(self, prev_track: TrackInfo | None, prev_state: SpeakerState | None) -> None:
        """Сравнить prev с current и выпустить соответствующие события в HA bus."""
        # Track / playback
        cur_t = self.track
        if cur_t is not None:
            prev_id = prev_track.track_id if prev_track else None
            if cur_t.track_id != prev_id:
                self.hass.bus.async_fire(EVENT_TRACK_CHANGED, {
                    **self._event_payload_base(),
                    "track_id": cur_t.track_id,
                    "title": cur_t.title,
                    "artists": list(cur_t.artists),
                    "album": cur_t.album,
                    "provider": cur_t.provider,
                    "previous_track_id": prev_id,
                })
            elif prev_track is not None and (
                prev_track.playing != cur_t.playing
                or prev_track.shuffle != cur_t.shuffle
                or prev_track.repeat != cur_t.repeat
            ):
                self.hass.bus.async_fire(EVENT_PLAYBACK_CHANGED, {
                    **self._event_payload_base(),
                    "track_id": cur_t.track_id,
                    "playing": cur_t.playing,
                    "shuffle": cur_t.shuffle,
                    "repeat": cur_t.repeat,
                })

        # Volume / mute
        cur_s = self.state
        if cur_s is not None and (
            prev_state is None
            or prev_state.volume_percent != cur_s.volume_percent
            or prev_state.muted != cur_s.muted
        ):
            self.hass.bus.async_fire(EVENT_VOLUME_CHANGED, {
                **self._event_payload_base(),
                "volume_percent": cur_s.volume_percent,
                "muted": cur_s.muted,
            })

    async def _async_update_data(self) -> dict[str, Any]:
        """Fallback poll — если push не приходят."""
        # Во время обрыва/реконнекта poll бессмысленен: запрос уйдёт в мёртвый
        # сокет. Супервизор уже переподключается — отдаём последний known state.
        if self.client.disconnected.is_set():
            return {"state": self.state, "track": self.track}
        await self._refresh_state_and_track(notify=False)
        return {"state": self.state, "track": self.track}
