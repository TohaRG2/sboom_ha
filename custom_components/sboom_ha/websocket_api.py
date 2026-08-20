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
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from ._deeplink import play_deeplink, send_server_action
from ._ha_helpers import get_zvuk_client, iter_coordinators
from .const import DOMAIN
from .coordinator import COMMAND_SPECS, SboomCoordinator
from .helpers import cover_url, sber_device_id
from .zvuk_client import ZvukClient

if TYPE_CHECKING:
    from ._models import SpeakerState, TrackInfo

_LOGGER = logging.getLogger(__name__)

# Свободный deeplink из фронтенда уходит на колонку как есть — принимаем
# только staros://-схему с безопасным набором символов (без пробелов/кавычек:
# инъекция параметров и разрыв payload'а невозможны). Аудит #41.
_DEEPLINK_RE = re.compile(r"^staros://[\w.~%/?&=:-]+$")


def _is_valid_deeplink(deeplink: str) -> bool:
    """True, если строка — безопасный staros:// deeplink."""
    return bool(_DEEPLINK_RE.match(deeplink))


# ─────────────────────────── доступ к состоянию ───────────────────────────


def _get_coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> SboomCoordinator | None:
    """Координатор выбранной колонки (по entry_id) или первой доступной.

    Колонок может быть несколько — панель адресует команды по entry_id.
    """
    for entry, coordinator in iter_coordinators(hass):
        if entry_id is None or entry.entry_id == entry_id:
            return coordinator
    return None


# ─────────────────────────── сериализация ─────────────────────────────────


def _serialize_track(
    track: TrackInfo | None, fallback_cover: str | None = None
) -> dict[str, Any] | None:
    """TrackInfo → JSON-safe dict для панели (плоский now-playing).

    fallback_cover — обложка, найденная по title+artist (BT/радио, у которых
    нет каталожного id) — тот же фолбэк, что в media_player и camera.
    """
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
        # Часы HA в момент получения снимка: часы колонки (position_ts_ms)
        # могут расходиться с реальностью — фронтенд предпочитает эту метку.
        "received_ts_ms": (
            int(track.received_ts * 1000) if track.received_ts else None
        ),
        "playing": track.playing,
        "shuffle": track.shuffle,
        "repeat": track.repeat,
        "explicit": track.explicit,
        "liked": track.liked,
        "has_lyrics": track.has_lyrics,
        "playback_speed": track.playback_speed,
        "cover_url": cover_url(track) or fallback_cover,
    }


def _serialize_state(state: SpeakerState | None) -> dict[str, Any] | None:
    """SpeakerState → JSON-safe dict (без сырого raw_state_json)."""
    if state is None:
        return None
    return {
        "volume_percent": state.volume_percent,
        "muted": state.muted,
    }


# ─────────────────────────── команды ──────────────────────────────────────


def _state_payload(
    hass: HomeAssistant, coordinator: SboomCoordinator
) -> dict[str, Any]:
    """JSON-safe снимок состояния колонки для панели (state + now-playing)."""
    return {
        "connected": coordinator.connected,
        "version": hass.data.get(f"{DOMAIN}_version"),
        "state": _serialize_state(coordinator.state),
        "track": _serialize_track(coordinator.track, coordinator.current_cover()),
    }


@websocket_api.websocket_command({vol.Required("type"): "sboom/devices"})
@callback
def ws_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Список доступных колонок для селектора панели.

    `serial` (= Sber device_id) — мост к сущностям настроек/эквалайзера
    интеграции sberhome: у поженённого HA-устройства общий identifier
    ``("sber_speaker", serial)``, по нему фронтенд находит эквалайзер.
    """
    devices = [
        {
            "entry_id": entry.entry_id,
            "name": entry.title,
            "serial": sber_device_id(entry),
        }
        for entry, _ in iter_coordinators(hass)
    ]
    connection.send_result(msg["id"], {"devices": devices})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/state",
        vol.Optional("entry_id"): vol.Any(str, None),
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
        vol.Optional("entry_id"): vol.Any(str, None),
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

    @callback
    def _on_stop() -> None:
        # Координатор останавливается (reload/unload entry) — терминальное
        # событие, по которому фронтенд переподписывается на новый инстанс
        # (иначе подписка замирала бы на мёртвом координаторе — аудит #32).
        connection.send_message(
            websocket_api.event_message(msg["id"], {"terminated": True})
        )

    unsub_update = coordinator.async_add_listener(_forward)
    unsub_stop = coordinator.async_add_stop_listener(_on_stop)

    @callback
    def _unsubscribe() -> None:
        unsub_update()
        unsub_stop()

    connection.subscriptions[msg["id"]] = _unsubscribe
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
    zvuk = get_zvuk_client(hass)
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
    zvuk = get_zvuk_client(hass)
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
    zvuk = get_zvuk_client(hass)
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
        vol.Optional("entry_id"): vol.Any(str, None),
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
    if deeplink is not None and not _is_valid_deeplink(deeplink):
        connection.send_error(
            msg["id"], "invalid_args", "invalid deeplink"
        )
        return
    if deeplink is None and (url := msg.get("url")):
        # Разбор/валидация zvuk-URL — единый источник в ZvukClient (хост
        # zvuk.com, известный kind, числовой id).
        parsed = ZvukClient.parse_zvuk_url(url)
        if parsed is None:
            connection.send_error(
                msg["id"], "invalid_args", f"Unrecognized url: {url}"
            )
            return
        deeplink = ZvukClient.build_deeplink(*parsed)
    if deeplink is None and (item_id := msg.get("content_id")):
        pt = msg.get("pt") or msg.get("kind")
        if not pt:
            connection.send_error(
                msg["id"], "invalid_args", "id requires kind or pt"
            )
            return
        deeplink = ZvukClient.deeplink_for(pt, item_id)
        if deeplink is None:
            connection.send_error(
                msg["id"], "invalid_args", f"invalid pt/id: {pt}/{item_id}"
            )
            return
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
    zvuk = get_zvuk_client(hass)
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
    zvuk = get_zvuk_client(hass)
    ids: list[str] = msg["ids"]
    try:
        tracks = await zvuk.get_tracks(ids)
    except Exception as exc:
        _LOGGER.debug("sboom/track_meta failed for %s: %s", ids, exc)
        connection.send_error(msg["id"], "zvuk_error", str(exc))
        return
    connection.send_result(msg["id"], {"tracks": tracks})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/command",
        vol.Required("action"): vol.In(sorted(COMMAND_SPECS)),
        vol.Optional("value"): vol.Any(int, float, bool, str),
        vol.Optional("entry_id"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_command(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """media/volume-команды колонки (play/pause/volume/seek/shuffle/…).

    Тонкий адаптер над единым командным слоем coordinator.async_execute
    (аудит #18): optimistic-патч и refresh-политика — там, панель получает
    мгновенное обновление через async_update_listeners.
    """
    coordinator = _get_coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return

    try:
        await coordinator.async_execute(msg["action"], msg.get("value"))
    except (ValueError, TypeError) as exc:
        connection.send_error(msg["id"], "invalid_args", str(exc))
        return
    except Exception as exc:
        _LOGGER.debug("sboom/command %s failed: %s", msg["action"], exc)
        connection.send_error(msg["id"], "command_failed", str(exc))
        return
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sboom/queue",
        vol.Optional("entry_id"): vol.Any(str, None),
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
            for track in await get_zvuk_client(hass).get_tracks(ids):
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
