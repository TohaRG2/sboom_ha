"""
Низкоуровневый WebSocket-клиент для локального LAN-управления колонкой.

Обмен с колонкой SberBoom через wss://<host>:20000/ — WebSocket поверх TLS,
бинарный payload в формате tag-length-value (varint + length-delimited).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import ssl
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from ._models import BluetoothDevice, QueueTrack, SpeakerState, TrackInfo
from ._parsers import parse_paired_bt as _parse_paired_bt
from ._parsers import parse_queue as _parse_queue
from ._parsers import parse_scanned_bt as _parse_scanned_bt
from ._parsers import parse_state as _parse_state
from ._parsers import parse_track as _parse_track
from ._tlv import decode as _decode_tlv
from ._tlv import field as _field
from .const import (
    DEFAULT_PORT,
    DEFAULT_USER_AGENT,
    ENVELOPE_FIELD_MSG_ID,
    ENVELOPE_FIELD_REQUEST_DATA,
    MEDIA_CMD_DISLIKE,
    MEDIA_CMD_LIKE,
    MEDIA_CMD_MUTE,
    MEDIA_CMD_NEXT,
    MEDIA_CMD_PAUSE,
    MEDIA_CMD_PLAY,
    MEDIA_CMD_PREV,
    MEDIA_CMD_REMOVE_DISLIKE,
    MEDIA_CMD_REMOVE_LIKE,
    MEDIA_CMD_REPEAT_NONE,
    MEDIA_CMD_REPEAT_PLAYLIST,
    MEDIA_CMD_REPEAT_TRACK,
    MEDIA_CMD_SHUFFLE_OFF,
    MEDIA_CMD_SHUFFLE_ON,
    MEDIA_CMD_UNMUTE,
    OP_BT_DEVICE_COMMAND,
    OP_BT_DISCOVERABLE,
    OP_FIND_REMOTE,
    OP_GET_META_DATA,
    OP_GET_PAIRED_BT,
    OP_GET_PLAYING_QUEUE,
    OP_GET_SCANNED_BT,
    OP_GET_STATE,
    OP_KEEP_ALIVE,
    OP_MEDIA_COMMAND,
    OP_PIN_CONNECT,
    OP_SET_PLAYBACK_SPEED,
    OP_SET_TRACK_POS,
    OP_SET_VOLUME,
    PAIR_BUTTON_TIMEOUT_SEC,
    PAIR_CONFIRM_OK,
    PAIR_CONFIRM_REJECTED,
    PAIR_STATUS_AUTHORIZED,
    PAIR_STATUS_BUSY,
    PAIR_STATUS_DISABLED,
    PAIR_STATUS_WAITING,
    PLAYBACK_SPEED_MAX,
    PLAYBACK_SPEED_MIN,
    REPEAT_TO_CANONICAL,
    TOKEN_TYPE_PIN_AUTH,
    WS_PING_INTERVAL_SEC,
    WS_PING_TIMEOUT_SEC,
)


def _detect_headers_kwarg() -> str:
    """websockets ≥ 13 → additional_headers; legacy → extra_headers."""
    try:
        params = inspect.signature(websockets.connect).parameters
        if "additional_headers" in params:
            return "additional_headers"
    except (ValueError, TypeError):
        pass
    return "extra_headers"


_HEADERS_KWARG = _detect_headers_kwarg()

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Pair-handshake и команды
# ─────────────────────────────────────────────────────────────────────

class PairTimeout(Exception):
    """Колонка не подтвердила pair (нет нажатия '+' за timeout)."""


class AuthError(Exception):
    """Колонка отвергла наш токен/UUID."""


class SberSpeakerClient:
    """Persistent WS-клиент колонки.

    Вызовы доступные после успешного `connect()`:
      get_state(), get_metadata(), get_queue()
      set_volume(), media_play(), media_pause(), media_next(), media_prev()
      seek_to(sec), keep_alive()

    Также можно подписаться на async state-updates через callback в `__init__`.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        client_id: str | None = None,
        client_name: str = "Home Assistant",
        pin_access_token: str | None = None,
        on_event: Callable[[bytes, dict[int, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id or str(uuid.uuid4())
        self.client_name = client_name
        self.pin_access_token = pin_access_token
        self._on_event = on_event

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._listener_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        # Выставляется при выходе из _listen_loop (обрыв связи). Супервизор
        # ждёт это событие, чтобы среагировать на разрыв мгновенно, не дожидаясь
        # следующего keepalive-цикла. connect() его сбрасывает.
        self._disconnected = asyncio.Event()
        self._disconnected.set()  # до первого connect() клиент не подключён

    # ────────────────────────────── connection ──────────────────────────────

    @staticmethod
    def _make_ssl_context() -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def connect(self) -> None:
        # ssl.create_default_context() читает CA bundle с диска (blocking I/O).
        # HA в dev-mode логирует/блокирует это, поэтому уходим в executor.
        ssl_ctx = await asyncio.to_thread(self._make_ssl_context)

        url = f"wss://{self.host}:{self.port}/"
        _LOGGER.debug("connecting to %s as client_id=%s", url, self.client_id)
        connect_kwargs: dict[str, Any] = {
            "ssl": ssl_ctx,
            "max_size": 2**20,
            "open_timeout": 10,
            # Транспортный heartbeat: pong обязателен по RFC 6455, поэтому
            # безответный ping = half-open TCP → библиотека сама закрывает
            # соединение, _listen_loop завершается, супервизор реконнектит.
            "ping_interval": WS_PING_INTERVAL_SEC,
            "ping_timeout": WS_PING_TIMEOUT_SEC,
            _HEADERS_KWARG: [("User-Agent", DEFAULT_USER_AGENT)],
        }
        self._ws = await websockets.connect(url, **connect_kwargs)
        self._disconnected.clear()
        # NB: listener НЕ стартуется автоматически — он конфликтует с прямыми
        # recv()-вызовами в pair_with_button. После завершения pair (или сразу
        # при reconnect к спареной колонке) надо вызвать start_listening().

    def start_listening(self) -> None:
        """Запускает фоновый dispatcher async-сообщений от колонки.
        Должен быть вызван ПОСЛЕ pair_with_button() (если pair проходил)
        либо сразу после connect() для уже спареной колонки.
        """
        if self._listener_task and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(self._listen_loop(), name="sboom-listener")

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # pragma: no cover
                pass
            self._ws = None
        self._disconnected.set()

    @property
    def disconnected(self) -> asyncio.Event:
        """Событие обрыва: set когда listen-loop завершился, clear после connect().

        Супервизор ждёт его, чтобы реагировать на разрыв сразу; poll сверяется
        с ним, чтобы не слать запросы в мёртвый сокет.
        """
        return self._disconnected

    # ────────────────────────────── pair flow ──────────────────────────────

    async def pair_with_button(self) -> str:
        """Запустить pair-handshake. Пользователь должен нажать '+' на колонке.

        Возвращает свежий `pin_access_token`. Также сохраняет его в self.
        """
        if not self._ws:
            raise RuntimeError("not connected")
        # pair-flow читает ответы прямым recv() в цикле — это конфликтует с
        # фоновым listen-loop, который перехватил бы те же сообщения.
        if self._listener_task and not self._listener_task.done():
            raise RuntimeError(
                "pair_with_button нельзя вызывать при активном listen-loop"
            )

        # отправляем pair-init (пустой запрос)
        cast = _field(OP_PIN_CONNECT, 2, _field(1, 2, b""))
        req_id = str(uuid.uuid4())
        pkt = self._envelope(req_id, cast)
        await self._ws.send(pkt)
        _LOGGER.info("pair: init sent (req_id=%s, %d bytes) — нажмите '+'",
                     req_id, len(pkt))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + PAIR_BUTTON_TIMEOUT_SEC
        msg_idx = 0
        while loop.time() < deadline:
            remain = deadline - loop.time()
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=remain)
            except TimeoutError:
                _LOGGER.warning("pair: timed out waiting for pair-confirm response")
                break
            msg_idx += 1
            parsed = _decode_tlv(resp if isinstance(resp, (bytes, bytearray)) else resp.encode())
            # ВАЖНО: сырые parsed-поля pair-ответов НЕ логировать — они содержат
            # pin-токен (полный доступ к колонке), а лог часто прикладывают к issue.
            _LOGGER.debug("pair: msg #%d (%d bytes)", msg_idx, len(resp))

            req_data = parsed.get(ENVELOPE_FIELD_REQUEST_DATA)
            if not isinstance(req_data, dict):
                continue

            # Первый ответ (поле 4): статус-код в sub[1], опц. данные в sub[2].
            pin_resp = req_data.get(4)
            if isinstance(pin_resp, dict):
                status = pin_resp.get(1)
                if status == PAIR_STATUS_AUTHORIZED:
                    token = pin_resp.get(2)
                    if isinstance(token, str) and len(token) >= 16:
                        _LOGGER.info("pair: token received (init-stage), %d chars", len(token))
                        self.pin_access_token = token
                        return token
                elif status == PAIR_STATUS_WAITING:
                    sess = pin_resp.get(2)
                    _LOGGER.info(
                        "pair: waiting for '+' button press (session=%s)", sess
                    )
                    # просто ждём — колонка пришлёт следующий ответ после нажатия
                    continue
                elif status == PAIR_STATUS_BUSY:
                    raise PairTimeout("pair: session already active")
                elif status == PAIR_STATUS_DISABLED:
                    raise PairTimeout("pair: mode disabled on speaker")

            # Второй ответ (поле 6) — после нажатия "+".
            confirm = req_data.get(6)
            if isinstance(confirm, dict):
                status = confirm.get(1)
                if status == PAIR_CONFIRM_OK:
                    token = confirm.get(2)
                    if isinstance(token, str) and len(token) >= 16:
                        _LOGGER.info("pair: token received (confirm-stage), %d chars", len(token))
                        self.pin_access_token = token
                        return token
                elif status == PAIR_CONFIRM_REJECTED:
                    raise PairTimeout("pair: rejected by speaker")
        raise PairTimeout("pair timed out — кнопка '+' не была нажата")

    # ────────────────────────────── high-level commands ──────────────────────────────

    async def get_state(self) -> SpeakerState | None:
        """None — колонка ответила, но payload не распарсился (state сохраняем)."""
        resp = await self._request_response(_field(OP_GET_STATE, 2, _field(1, 2, b"")))
        return self._extract_state(resp)

    async def get_metadata(self) -> TrackInfo | None:
        resp = await self._request_response(_field(OP_GET_META_DATA, 2, _field(1, 2, b"")))
        return self._extract_track(resp)

    async def get_queue(self) -> list[QueueTrack]:
        resp = await self._request_response(_field(OP_GET_PLAYING_QUEUE, 2, _field(1, 2, b"")))
        return _parse_queue(resp)

    async def get_paired_bt_devices(self) -> list[BluetoothDevice]:
        """op=19 — список спаренных Bluetooth-устройств колонки."""
        resp = await self._request_response(_field(OP_GET_PAIRED_BT, 2, _field(1, 2, b"")))
        return _parse_paired_bt(resp)

    async def get_scanned_bt_devices(self) -> list[BluetoothDevice]:
        """op=21 — список найденных при сканировании Bluetooth-устройств."""
        resp = await self._request_response(_field(OP_GET_SCANNED_BT, 2, _field(1, 2, b"")))
        return _parse_scanned_bt(resp)

    async def bt_device_command(self, mac: str, cmd: int) -> None:
        """op=20 — команда BT-устройству по MAC.

        cmd: BT_CMD_CONNECT / BT_CMD_DISCONNECT / BT_CMD_REMOVE.
        Request — {mac (поле 1), cmd (поле 2, varint)}.
        """
        inner = _field(1, 2, mac.encode()) + _field(2, 0, int(cmd))
        await self._fire_and_forget(_field(OP_BT_DEVICE_COMMAND, 2, inner))

    async def set_volume(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        cast = _field(OP_SET_VOLUME, 2, _field(1, 0, percent))
        await self._fire_and_forget(cast)

    async def media_play(self) -> None:
        await self._send_media_command(MEDIA_CMD_PLAY)

    async def media_pause(self) -> None:
        await self._send_media_command(MEDIA_CMD_PAUSE)

    async def media_next(self) -> None:
        await self._send_media_command(MEDIA_CMD_NEXT)

    async def media_prev(self) -> None:
        await self._send_media_command(MEDIA_CMD_PREV)

    async def media_mute(self) -> None:
        await self._send_media_command(MEDIA_CMD_MUTE)

    async def media_unmute(self) -> None:
        await self._send_media_command(MEDIA_CMD_UNMUTE)

    async def media_like(self) -> None:
        await self._send_media_command(MEDIA_CMD_LIKE)

    async def media_remove_like(self) -> None:
        await self._send_media_command(MEDIA_CMD_REMOVE_LIKE)

    async def media_dislike(self) -> None:
        await self._send_media_command(MEDIA_CMD_DISLIKE)

    async def media_remove_dislike(self) -> None:
        await self._send_media_command(MEDIA_CMD_REMOVE_DISLIKE)

    async def media_shuffle(self, on: bool) -> None:
        await self._send_media_command(MEDIA_CMD_SHUFFLE_ON if on else MEDIA_CMD_SHUFFLE_OFF)

    async def media_repeat(self, mode: str) -> None:
        """mode — любой синоним из REPEAT_TO_CANONICAL ('none'/'off', 'playlist'/'all', 'track'/'one')."""
        canon = REPEAT_TO_CANONICAL.get(mode.lower(), "off")
        cmd = {
            "off": MEDIA_CMD_REPEAT_NONE,
            "all": MEDIA_CMD_REPEAT_PLAYLIST,
            "one": MEDIA_CMD_REPEAT_TRACK,
        }[canon]
        await self._send_media_command(cmd)

    async def seek_to(self, position_sec: int) -> None:
        # seek-операция: единица — секунды (наблюдаемое поведение)
        cast = _field(OP_SET_TRACK_POS, 2, _field(1, 0, int(position_sec)))
        await self._fire_and_forget(cast)

    async def set_playback_speed(self, rate: float) -> None:
        """Скорость воспроизведения (op=23).

        modifier кодируется как float (wire-type 5, 4 байта LE IEEE-754).
        varint и nested-JSON ломают playbackSpeedRate колонки в 0.0
        (research exp_22), поэтому только float и обязательный clamp.
        """
        rate = max(PLAYBACK_SPEED_MIN, min(PLAYBACK_SPEED_MAX, float(rate)))
        cast = _field(OP_SET_PLAYBACK_SPEED, 2, _field(1, 5, rate))
        await self._fire_and_forget(cast)

    async def keep_alive(self) -> None:
        await self._fire_and_forget(_field(OP_KEEP_ALIVE, 2, _field(1, 2, b"")))

    async def find_remote(self) -> None:
        """op=13 — команда поиска пульта ДУ. Request — пустое сообщение."""
        await self._fire_and_forget(_field(OP_FIND_REMOTE, 2, _field(1, 2, b"")))

    async def bt_make_discoverable(self) -> None:
        """op=22 — включить режим Bluetooth-сопряжения.

        Request — пустое сообщение. Длительность окна видимости задаёт
        прошивка колонки (протоколом не управляется).
        """
        await self._fire_and_forget(_field(OP_BT_DISCOVERABLE, 2, _field(1, 2, b"")))

    # ────────────────────────────── internals ──────────────────────────────

    async def _send_media_command(self, action: int) -> None:
        # медиа-команда: action — опкод из MEDIA_CMD_*, поле 1 в media-command-операции
        request_inner = _field(1, 0, action)
        cast = _field(OP_MEDIA_COMMAND, 2, request_inner)
        await self._fire_and_forget(cast)

    def _envelope(self, req_id: str, request_data: bytes) -> bytes:
        parts = [
            _field(1, 0, 2),                                 # type=REQUEST
            _field(2, 2, req_id.encode()),                   # id
        ]
        if self.pin_access_token:
            parts.append(_field(3, 2, self.pin_access_token.encode()))
        parts += [
            _field(5, 2, request_data),                      # request_data
            _field(6, 0, TOKEN_TYPE_PIN_AUTH),               # token_type
            _field(7, 2, self.client_name.encode()),         # client_name
            _field(10, 0, 1),                                # is_request=true
            _field(11, 2, self.client_id.encode()),          # client_id
        ]
        return b"".join(parts)

    async def _fire_and_forget(self, request_data: bytes) -> None:
        # Локальная ссылка: параллельный close() может обнулить self._ws
        # между проверкой и send — тогда был бы AttributeError вместо
        # ConnectionClosed, который супервизор умеет обрабатывать.
        ws = self._ws
        if not ws:
            raise RuntimeError("not connected")
        async with self._lock:
            await ws.send(self._envelope(str(uuid.uuid4()), request_data))

    async def _request_response(self, request_data: bytes, timeout: float = 5.0) -> bytes:
        ws = self._ws
        if not ws:
            raise RuntimeError("not connected")
        req_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            async with self._lock:
                await ws.send(self._envelope(req_id, request_data))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _listen_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    msg = msg.encode()
                parsed = _decode_tlv(msg)
                # сматчить с pending по id, либо считать unsolicited
                msg_id = parsed.get(ENVELOPE_FIELD_MSG_ID)
                if isinstance(msg_id, str) and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                    continue

                if self._on_event:
                    try:
                        await self._on_event(msg, parsed)
                    except Exception:  # pragma: no cover
                        _LOGGER.exception("on_event handler raised")
        except asyncio.CancelledError:
            raise
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            # Штатный обрыв долгоживущего WS (колонка перезагрузилась, Wi-Fi
            # мигнул, idle-disconnect). Не исключение — INFO без traceback.
            _LOGGER.info("WS closed (%s) — переподключение", exc.__class__.__name__)
        except Exception:
            _LOGGER.exception("listen loop crashed")
        finally:
            # Разбудить супервизор: связь мертва, нужно реконнектиться.
            self._disconnected.set()
            # Отменить ожидающие запросы, чтобы они не висели до таймаута.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("WS closed"))
            self._pending.clear()

    # ────────────────────────────── parsers ──────────────────────────────

    @staticmethod
    def parse_state(raw: bytes) -> SpeakerState | None:
        """Public wrapper — делегирует в _parsers.parse_state."""
        return _parse_state(raw)

    @staticmethod
    def parse_track(raw: bytes) -> TrackInfo | None:
        """Public wrapper — делегирует в _parsers.parse_track."""
        return _parse_track(raw)

    @staticmethod
    def parse_queue(raw: bytes) -> list[QueueTrack]:
        """Public wrapper — делегирует в _parsers.parse_queue."""
        return _parse_queue(raw)

    @staticmethod
    def parse_paired_bt(raw: bytes) -> list[BluetoothDevice]:
        """Public wrapper — делегирует в _parsers.parse_paired_bt."""
        return _parse_paired_bt(raw)

    @staticmethod
    def parse_scanned_bt(raw: bytes) -> list[BluetoothDevice]:
        """Public wrapper — делегирует в _parsers.parse_scanned_bt."""
        return _parse_scanned_bt(raw)

    # backwards-compat алиасы для внутренних вызовов в этом классе
    _extract_state = staticmethod(_parse_state)
    _extract_track = staticmethod(_parse_track)
