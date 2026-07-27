"""WebSocket API для встроенной панели sboom_ha.

Мост frontend ↔ backend: панель зовёт эти команды через
``hass.callWS({type: "sboom/..."})`` и получает результат из
``connection.send_result``. По образцу референс-панели ha-sberhome
(custom_components/sberhome/websocket_api), но всё в одном модуле —
у sboom_ha команд немного.

Команды (type):
- ``sboom/state``      — текущий now-playing + громкость + состояние из coordinator.
- ``sboom/search``     — {query} → ZvukClient.search (каталог Sber Звук).
- ``sboom/play``       — {deeplink | url | id + kind/pt} → собрать staros-deeplink
                          и проиграть его на колонке через play_deeplink.
- ``sboom/track_meta`` — {ids} → ZvukClient.get_tracks (обогащение метаданными).
- ``sboom/command``    — {action, value} → media/volume-команды колонки.

Доступ к координатору — через ``entry.runtime_data`` (IQS bronze runtime-data,
как в services.py). ZvukClient — один инстанс, кешируется в hass.data.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from ._deeplink import play_deeplink, send_server_action
from .const import DOMAIN
from .coordinator import SboomCoordinator
from .helpers import cover_url
from .zvuk_client import ZvukClient

if TYPE_CHECKING:
    from ._models import SpeakerState, TrackInfo

_LOGGER = logging.getLogger(__name__)

_ZVUK_CLIENT_KEY = f"{DOMAIN}_zvuk_client"

# pt (playlist type) → в какое поле deeplink кладётся id.
# tid — конкретный трек/подкаст-выпуск; pid — коллекция (артист/плейлист/релиз).
_PT_TID = frozenset({"track", "podcast"})

# zvuk.com/{path}/<id> → pt (playlist type) для сборки deeplink.
# release==album (pt=release — по фактам реверса, помечен как непроверенный).
_ZVUK_URL_KIND_TO_PT = {
    "track": "track",
    "artist": "artist",
    "release": "release",
    "playlist": "playlist",
    "abook": "podcast",
}


# ─────────────────────────── доступ к состоянию ───────────────────────────


def _iter_coordinators(hass: HomeAssistant):
    """(entry, coordinator) для всех загруженных колонок SberBoom."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, SboomCoordinator):
            yield entry, coordinator


def _get_coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> SboomCoordinator | None:
    """Координатор выбранной колонки (по entry_id) или первой доступной.

    Колонок может быть несколько — панель адресует команды по entry_id.
    """
    for entry, coordinator in _iter_coordinators(hass):
        if entry_id is None or entry.entry_id == entry_id:
            return coordinator
    return None


def _get_zvuk_client(hass: HomeAssistant) -> ZvukClient:
    """Единственный кешированный ZvukClient (ленивая инициализация).

    Отдельный HTTP-клиент со своим cookie jar (anti-bot Звука: 307-редирект +
    cookie ``spid``), поэтому не переиспользуем shared aiohttp-сессию HA.
    """
    client: ZvukClient | None = hass.data.get(_ZVUK_CLIENT_KEY)
    if client is None:
        client = ZvukClient()
        hass.data[_ZVUK_CLIENT_KEY] = client
    return client


# ─────────────────────────── сериализация ─────────────────────────────────


def _serialize_track(track: TrackInfo | None) -> dict[str, Any] | None:
    """TrackInfo → JSON-safe dict для панели (плоский now-playing)."""
    if track is None:
        return None
    return {
        "title": track.title,
        "artists": list(track.artists),
        "album": track.album,
        "track_id": track.track_id,
        "release_id": track.release_id,
        "artist_ids": list(track.artist_ids),
        "playlist_title": track.playlist_title,
        "playlist_type": track.playlist_type,
        "playlist_id": track.playlist_id,
        "media_source": track.media_source,
        "station_name": track.station_name,
        "provider": track.provider,
        "duration_sec": track.duration_sec,
        "position_sec": track.position_sec,
        # снимок позиции + метка времени (unix ms) — панель крутит прогресс
        # локально от этой точки, как media_player.media_position_updated_at.
        "position_ts_ms": track.position_ts_ms,
        "playing": track.playing,
        "shuffle": track.shuffle,
        "repeat": track.repeat,
        "explicit": track.explicit,
        "liked": track.liked,
        "has_lyrics": track.has_lyrics,
        "playback_speed": track.playback_speed,
        "cover_url": cover_url(track),
    }


def _serialize_state(state: SpeakerState | None) -> dict[str, Any] | None:
    """SpeakerState → JSON-safe dict (без сырого raw_state_json)."""
    if state is None:
        return None
    return {
        "volume_percent": state.volume_percent,
        "muted": state.muted,
    }


# ─────────────────────────── deeplink helpers ─────────────────────────────


def _build_deeplink(pt: str, item_id: str) -> str:
    """Собрать staros-deeplink из pt (playlist type) и id.

    track/podcast → tid, всё остальное (artist/playlist/release) → pid.
    """
    key = "tid" if pt in _PT_TID else "pid"
    return f"staros://music?{key}={item_id}&pt={pt}"


def _deeplink_from_zvuk_url(url: str) -> str | None:
    """zvuk.com/{track|artist|release|playlist|abook}/<id> → staros-deeplink."""
    parsed = urlparse(url)
    segments = [seg for seg in parsed.path.split("/") if seg]
    if len(segments) < 2:
        return None
    kind, item_id = segments[-2], segments[-1]
    pt = _ZVUK_URL_KIND_TO_PT.get(kind)
    if pt is None or not item_id:
        return None
    return _build_deeplink(pt, item_id)


# ─────────────────────────── команды ──────────────────────────────────────


def _state_payload(
    hass: HomeAssistant, coordinator: SboomCoordinator
) -> dict[str, Any]:
    """JSON-safe снимок состояния колонки для панели (state + now-playing)."""
    return {
        "connected": coordinator.connected,
        "version": hass.data.get(f"{DOMAIN}_version"),
        "state": _serialize_state(coordinator.state),
        "track": _serialize_track(coordinator.track),
    }


@websocket_api.websocket_command({vol.Required("type"): "sboom/devices"})
@callback
def ws_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Список доступных колонок для селектора панели."""
    devices = [
        {"entry_id": entry.entry_id, "name": entry.title}
        for entry, _ in _iter_coordinators(hass)
    ]
    connection.send_result(msg["id"], {"devices": devices})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/state",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_state(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Текущее состояние колонки: now-playing + громкость (разовый запрос)."""
    coordinator = _get_coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    connection.send_result(msg["id"], _state_payload(hass, coordinator))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/subscribe",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Push-подписка на состояние колонки.

    Регистрирует слушателя координатора: тот шлёт `async_update_listeners()`
    на КАЖДОМ push-обновлении от колонки (смена трека, play/pause, громкость),
    и панель получает свежее состояние мгновенно — без 5-сек поллинга.
    Сразу после подписки отправляется текущее состояние.
    """
    coordinator = _get_coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return

    @callback
    def _forward() -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], _state_payload(hass, coordinator)
            )
        )

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(_forward)
    connection.send_result(msg["id"])
    _forward()  # начальное состояние


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/search",
        vol.Required("query"): str,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=100)),
    }
)
@websocket_api.async_response
async def ws_search(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Поиск по каталогу Sber Звук.

    Возвращает категоризированный результат: {best, artists, releases,
    tracks, playlists}. Каждый элемент — {id, type, title, subtitle,
    cover_url, pt, explicit, duration}.
    """
    zvuk = _get_zvuk_client(hass)
    query: str = msg["query"]
    limit: int = msg.get("limit", 8)
    try:
        results = await zvuk.search(query, limit=limit)
    except Exception as exc:
        _LOGGER.debug("sboom/search failed for %r: %s", query, exc)
        connection.send_error(msg["id"], "zvuk_error", str(exc))
        return
    connection.send_result(msg["id"], results)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/artist",
        # content_id, НЕ "id": ключ "id" зарезервирован WS-протоколом HA.
        vol.Required("content_id"): str,
    }
)
@websocket_api.async_response
async def ws_artist(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Детали артиста (drill-down): релизы + топ-треки из каталога Звука."""
    zvuk = _get_zvuk_client(hass)
    try:
        artist = await zvuk.get_artist(msg["content_id"])
    except Exception as exc:
        _LOGGER.debug("sboom/artist failed for %s: %s", msg["content_id"], exc)
        connection.send_error(msg["id"], "zvuk_error", str(exc))
        return
    if artist is None:
        connection.send_error(msg["id"], "not_found", "Artist not found")
        return
    connection.send_result(msg["id"], artist)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/release",
        # content_id, НЕ "id": ключ "id" зарезервирован WS-протоколом HA.
        vol.Required("content_id"): str,
    }
)
@websocket_api.async_response
async def ws_release(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Детали релиза (drill-down): шапка + треклист из каталога Звука."""
    zvuk = _get_zvuk_client(hass)
    try:
        release = await zvuk.get_release(msg["content_id"])
    except Exception as exc:
        _LOGGER.debug("sboom/release failed for %s: %s", msg["content_id"], exc)
        connection.send_error(msg["id"], "zvuk_error", str(exc))
        return
    if release is None:
        connection.send_error(msg["id"], "not_found", "Release not found")
        return
    connection.send_result(msg["id"], release)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/play",
        vol.Exclusive("deeplink", "target"): str,
        vol.Exclusive("url", "target"): str,
        # content_id — НЕ "id": ключ "id" зарезервирован WS-протоколом HA
        # (номер сообщения, int) и вызывает конфликт схемы.
        vol.Exclusive("content_id", "target"): str,
        # kind / pt — синонимы (pt = playlist type в терминах Звука).
        vol.Optional("kind"): str,
        vol.Optional("pt"): str,
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_play(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Проиграть контент на колонке по deeplink / zvuk-URL / (id + kind|pt)."""
    coordinator = _get_coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return

    deeplink: str | None = msg.get("deeplink")
    if deeplink is None and (url := msg.get("url")):
        deeplink = _deeplink_from_zvuk_url(url)
        if deeplink is None:
            connection.send_error(
                msg["id"], "invalid_args", f"Unrecognized url: {url}"
            )
            return
    if deeplink is None and (item_id := msg.get("content_id")):
        pt = msg.get("pt") or msg.get("kind")
        if not pt:
            connection.send_error(
                msg["id"], "invalid_args", "id requires kind or pt"
            )
            return
        deeplink = _build_deeplink(pt, item_id)
    if deeplink is None:
        connection.send_error(
            msg["id"], "invalid_args", "one of deeplink/url/id is required"
        )
        return

    try:
        response = await play_deeplink(coordinator.client, deeplink)
    except Exception as exc:
        _LOGGER.debug("sboom/play failed for %r: %s", deeplink, exc)
        connection.send_error(msg["id"], "command_failed", str(exc))
        return
    connection.send_result(
        msg["id"], {"success": True, "deeplink": deeplink, "response": response}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/cover_color",
        vol.Required("url"): str,
    }
)
@websocket_api.async_response
async def ws_cover_color(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Доминирующий цвет обложки (hex) для ambient-glow панели.

    Считается на сервере (CDN Звука без CORS → клиентский canvas невозможен),
    кешируется по URL в ZvukClient.
    """
    zvuk = _get_zvuk_client(hass)
    try:
        color = await zvuk.dominant_cover_color(msg["url"])
    except Exception as exc:
        _LOGGER.debug("sboom/cover_color failed: %s", exc)
        connection.send_result(msg["id"], {"color": None})
        return
    connection.send_result(msg["id"], {"color": color})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/track_meta",
        vol.Required("ids"): vol.All([str], vol.Length(min=1)),
    }
)
@websocket_api.async_response
async def ws_track_meta(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Метаданные треков по id из каталога Звука (обложки/исполнители/длит.)."""
    zvuk = _get_zvuk_client(hass)
    ids: list[str] = msg["ids"]
    try:
        tracks = await zvuk.get_tracks(ids)
    except Exception as exc:
        _LOGGER.debug("sboom/track_meta failed for %s: %s", ids, exc)
        connection.send_error(msg["id"], "zvuk_error", str(exc))
        return
    connection.send_result(msg["id"], {"tracks": tracks})


# action → (метод клиента, нужен ли value). value_kind: None|"int"|"float"|"bool"|"str".
_MEDIA_ACTIONS: dict[str, tuple[str, str | None]] = {
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


def _coerce_value(kind: str, value: Any) -> Any:
    """Привести value из JSON к типу, ожидаемому методом клиента."""
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "bool":
        return bool(value)
    return str(value)


def _apply_optimistic(
    coordinator: SboomCoordinator, action: str, value: Any
) -> None:
    """Оптимистично отразить команду в состоянии — мгновенно в панели.

    Зеркалит media_player: координатор патчит локальный TrackInfo/SpeakerState
    и шлёт ``async_update_listeners()`` → подписанная панель обновляется сразу,
    не дожидаясь реального push от колонки (иначе кнопки «залипают»/откатывают).
    next/prev/seek — без патча: трек/позиция придут push'ем, а скраббер панель
    двигает сама.
    """
    if action == "play":
        coordinator.apply_optimistic_track(playing=True)
    elif action == "pause":
        coordinator.apply_optimistic_track(playing=False)
    elif action == "like":
        coordinator.apply_optimistic_track(liked=True)
    elif action == "remove_like":
        coordinator.apply_optimistic_track(liked=False)
    elif action == "dislike":
        coordinator.apply_optimistic_track(liked=False)
    elif action == "mute":
        coordinator.apply_optimistic_state(muted=True)
    elif action == "unmute":
        coordinator.apply_optimistic_state(muted=False)
    elif action == "volume" and value is not None:
        coordinator.apply_optimistic_state(volume_percent=int(value))
    elif action == "shuffle" and value is not None:
        coordinator.apply_optimistic_track(shuffle=bool(value))
    elif action == "repeat" and value is not None:
        coordinator.apply_optimistic_track(repeat=str(value))
    elif action == "playback_speed" and value is not None:
        coordinator.apply_optimistic_track(playback_speed=float(value))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/command",
        vol.Required("action"): vol.In(sorted(_MEDIA_ACTIONS)),
        vol.Optional("value"): vol.Any(int, float, bool, str),
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_command(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """media/volume-команды колонки (play/pause/volume/seek/shuffle/…)."""
    coordinator = _get_coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return

    action = msg["action"]
    method_name, value_kind = _MEDIA_ACTIONS[action]
    method = getattr(coordinator.client, method_name)
    value: Any = None
    try:
        if value_kind is None:
            await method()
        else:
            if "value" not in msg:
                connection.send_error(
                    msg["id"], "invalid_args", f"{action} requires value"
                )
                return
            value = _coerce_value(value_kind, msg["value"])
            await method(value)
    except (ValueError, TypeError) as exc:
        connection.send_error(msg["id"], "invalid_args", str(exc))
        return
    except Exception as exc:
        _LOGGER.debug("sboom/command %s failed: %s", action, exc)
        connection.send_error(msg["id"], "command_failed", str(exc))
        return
    # мгновенно отразить в подписанной панели (не ждать push от колонки)
    _apply_optimistic(coordinator, action, value)
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/queue",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_queue(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Очередь воспроизведения (op17), обогащённая метаданными Звука.

    Колонка отдаёт только track_id — названия/обложки добираем из каталога
    Звука одним batch-запросом. Переключение на трек делается панелью через
    ``sboom/play`` с этим track_id (pt=track).
    """
    coordinator = _get_coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    try:
        queue = await coordinator.client.get_queue()
    except Exception as exc:
        _LOGGER.debug("sboom/queue get_queue failed: %s", exc)
        connection.send_error(msg["id"], "command_failed", str(exc))
        return

    ids = [q.track_id for q in queue if getattr(q, "track_id", None)]
    meta_by_id: dict[str, dict[str, Any]] = {}
    if ids:
        try:
            for track in await _get_zvuk_client(hass).get_tracks(ids):
                meta_by_id[str(track.get("id"))] = track
        except Exception as exc:  # обогащение best-effort — очередь важнее
            _LOGGER.debug("sboom/queue enrich failed: %s", exc)

    items = []
    for q in queue:
        tid = getattr(q, "track_id", None)
        meta = meta_by_id.get(str(tid), {})
        items.append(
            {
                "track_id": tid,
                "explicit": getattr(q, "explicit", None),
                "title": meta.get("title"),
                "artists": meta.get("artists"),
                "album": meta.get("album"),
                "cover_url": meta.get("cover_url"),
                "duration": meta.get("duration"),
            }
        )
    connection.send_result(msg["id"], {"queue": items})


_COMMANDS = (
    ws_devices,
    ws_state,
    ws_subscribe,
    ws_search,
    ws_artist,
    ws_release,
    ws_play,
    ws_track_meta,
    ws_queue,
    ws_command,
    ws_cover_color,
)


@callback
def async_setup_websocket_api(hass: HomeAssistant) -> None:
    """Идемпотентная регистрация WS-команд панели sboom_ha."""
    marker = f"{DOMAIN}_ws_registered"
    if hass.data.get(marker):
        return
    hass.data[marker] = True
    for command in _COMMANDS:
        websocket_api.async_register_command(hass, command)
    _LOGGER.debug("sboom_ha WebSocket API registered")


__all__ = [
    "async_setup_websocket_api",
    "play_deeplink",
    "send_server_action",
    "ws_artist",
    "ws_command",
    "ws_cover_color",
    "ws_devices",
    "ws_play",
    "ws_queue",
    "ws_release",
    "ws_search",
    "ws_state",
    "ws_subscribe",
    "ws_track_meta",
]
