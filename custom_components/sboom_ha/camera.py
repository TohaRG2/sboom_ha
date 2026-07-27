"""Camera-entity: рендерит обложку + lyrics для отправки на ТВ через media_player.play_media."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONTENT_TYPE_MULTIPART
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .const import LYRICS_FRAME_INTERVAL_SEC
from .coordinator import SboomCoordinator
from .helpers import cover_url, lyrics_position, source_label, track_position
from .image_render import (
    draw_blank,
    draw_cover_yandex,
    draw_lyrics_with_cover,
    fallback_cover,
    resize_jpeg,
)

_LOGGER = logging.getLogger(__name__)


def _cover_seed(track) -> str:
    """Стабильный ключ трека для выбора фона-заглушки (один трек → один фон)."""
    return track.track_id or f"{track.title or ''}|{','.join(track.artists or [])}"

# Один MJPEG-стрим на сущность, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    async_add_entities([SboomLyricsCamera(coordinator, entry)])


def _timeline_at(
    timeline: list[tuple[float, str]], pos: float
) -> tuple[int, str | None, str | None, float | None]:
    """Состояние лирики в позиции pos: (индекс, текущая, следующая, доля строки).

    idx = -1 до первой строки. frac — линейная доля прохождения текущей
    строки между её таймстампом и таймстампом следующей (для караоке-заливки);
    None для последней строки и до начала первой.
    """
    idx = -1
    for i, (ts, _text) in enumerate(timeline):
        if ts <= pos:
            idx = i
        else:
            break
    cur = timeline[idx][1] if idx >= 0 else None
    nxt = timeline[idx + 1][1] if idx + 1 < len(timeline) else None
    frac: float | None = None
    if 0 <= idx and idx + 1 < len(timeline):
        start, end = timeline[idx][0], timeline[idx + 1][0]
        if end > start:
            frac = (pos - start) / (end - start)
    return idx, cur, nxt, frac


class SboomLyricsCamera(SboomEntity, Camera):
    """MJPEG-стрим с обложкой и синхронизированными lyrics."""

    _attr_translation_key = "lyrics_camera"
    _attr_entity_registry_enabled_default = False  # включается вручную, тяжеловесная сущность

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        SboomEntity.__init__(self, coordinator, entry)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics_camera"

        # Cache: cover URL -> raw bytes. Ключ по URL, а не track_id: у BT/радио
        # track_id нет (None), и кэш по нему путал бы все некаталожные треки.
        self._cover_cache_url: str | None = None
        self._cover_raw: bytes | None = None
        # Cache: track_id -> готовый idle-JPEG (когда lyrics нет)
        self._idle_jpeg_track: str | None = None
        self._idle_jpeg: bytes | None = None

    # ─────────── Snapshot (для предпросмотра в HA) ───────────

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Snapshot текущего кадра: lyrics-кадр если есть synced-текст, иначе idle.

        width/height от HA (например, для превью) уважаем через resize.
        """
        jpeg = await self._build_lyrics_jpeg() or await self._build_idle_jpeg()
        if jpeg is None:
            jpeg = await asyncio.to_thread(draw_blank)
        if width or height:
            jpeg = await asyncio.to_thread(resize_jpeg, jpeg, width, height)
        return jpeg

    async def _build_lyrics_jpeg(self) -> bytes | None:
        """Одиночный lyrics-кадр на текущей позиции (для snapshot)."""
        track = self.coordinator.track
        lyrics = self.coordinator.current_lyrics()
        if not track or not lyrics or not lyrics.timeline:
            return None
        pos = lyrics_position(self.coordinator)
        if pos is None:
            return None
        _idx, cur, nxt, frac = _timeline_at(lyrics.timeline, pos)
        if cur is None and nxt is None:
            return None
        cover_raw = await self._fetch_cover_raw(track)
        artist = ", ".join(track.artists) if track.artists else None
        duration = float(track.duration_sec) if track.duration_sec else None
        progress = (pos / duration) if duration and duration > 0 else None
        fill = frac if (track.playing and self.coordinator.karaoke_fill) else None
        return await asyncio.to_thread(
            draw_lyrics_with_cover,
            cover_raw, cur, nxt, track.title, artist,
            progress, pos, duration,
            fill,
            source_label(track),
        )

    # ─────────── MJPEG stream (для отправки на ТВ) ───────────

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        response = web.StreamResponse()
        response.content_type = CONTENT_TYPE_MULTIPART.format("--frameboundary")
        await response.prepare(request)
        try:
            while True:
                try:
                    track = self.coordinator.track
                    if not track:
                        blank = await asyncio.to_thread(draw_blank)
                        await _write_jpeg(response, blank)
                        await asyncio.sleep(2)
                        continue
                    lyrics = self.coordinator.current_lyrics()
                    if lyrics and lyrics.timeline:
                        await self._stream_lyrics_with_cover(response)
                    else:
                        await self._stream_idle(response)
                except (asyncio.CancelledError, ConnectionResetError):
                    raise
                except Exception:
                    # Сбой рендера/отрисовки кадра не должен ронять весь стрим
                    # с HTTP 500 — логируем и пробуем снова после паузы.
                    _LOGGER.exception("lyrics-стрим: ошибка кадра, повтор через 2s")
                    await asyncio.sleep(2)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return response

    async def _stream_lyrics_with_cover(self, response: web.StreamResponse) -> None:
        """Караоке-стрим: lyrics поверх blur-обложки, ~5 FPS при воспроизведении.

        Кадр перерисовывается при смене (индекс строки, секунда позиции,
        корзина караоке-заливки). Сравнение по ИНДЕКСУ строки, а не тексту:
        повторяющиеся строки припева иначе не обновляли бы кадр (и next-line
        оставалась устаревшей). Секунда двигает таймер/прогресс-бар между
        строками; корзина заливки даёт плавную подсветку пропетой части.
        """
        track = self.coordinator.track
        if not track:
            return
        track_id = track.track_id
        lyrics = self.coordinator.current_lyrics()
        if not lyrics or not lyrics.timeline:
            return
        timeline = lyrics.timeline
        cover_raw = await self._fetch_cover_raw(track)
        artist = ", ".join(track.artists) if track.artists else None
        last_key: tuple | None = None
        last_pos: float | None = None

        while (
            self.coordinator.track
            and self.coordinator.track.track_id == track_id
            and self.coordinator.current_lyrics() is lyrics
        ):
            track = self.coordinator.track
            pos = lyrics_position(self.coordinator)
            if pos is None:
                await asyncio.sleep(1)
                continue
            # Анти-дрожание: свежий push может отдать позицию чуть меньше
            # экстраполированной — малый откат игнорируем (строка не мигает
            # назад у границы), большой (>1.5 c) — реальный seek, принимаем.
            if last_pos is not None and 0 < last_pos - pos < 1.5:
                pos = last_pos
            last_pos = pos

            idx, cur, nxt, frac = _timeline_at(timeline, pos)
            playing = bool(track.playing)
            # Закраска — опция (по умолчанию off): при off строка статична,
            # frac в рендер не идёт и не участвует в кэш-ключе (нет лишних кадров).
            fill = frac if (playing and self.coordinator.karaoke_fill) else None

            duration = float(track.duration_sec) if track.duration_sec else None
            progress = (pos / duration) if duration and duration > 0 else None
            # Корзины заливки: шаг 2.5% ширины строки — визуально плавно,
            # но без перерисовки кадров, где заливка не сдвинулась.
            frac_bucket = int(fill * 40) if fill is not None else None
            key = (idx, int(pos), frac_bucket)
            if key != last_key:
                jpeg = await asyncio.to_thread(
                    draw_lyrics_with_cover,
                    cover_raw, cur, nxt, track.title, artist,
                    progress, pos, duration,
                    fill,
                    source_label(track),
                )
                await _write_jpeg(response, jpeg)
                last_key = key

            await asyncio.sleep(LYRICS_FRAME_INTERVAL_SEC if playing else 1.0)

    async def _stream_idle(self, response: web.StreamResponse) -> None:
        """Нет synced lyrics — обновляем кадр каждую секунду (для движения прогресс-бара)."""
        track = self.coordinator.track
        track_id = track.track_id if track else None
        last_sec: int | None = None
        while (
            self.coordinator.track
            and self.coordinator.track.track_id == track_id
            and not (
                self.coordinator.current_lyrics()
                and self.coordinator.current_lyrics().timeline
            )
        ):
            pos = track_position(self.coordinator)
            cur_sec = int(pos) if pos is not None else None
            if cur_sec != last_sec:
                jpeg = await self._build_idle_jpeg()
                if jpeg:
                    await _write_jpeg(response, jpeg)
                last_sec = cur_sec
            await asyncio.sleep(1)

    async def _build_idle_jpeg(self) -> bytes | None:
        track = self.coordinator.track
        if not track:
            return None
        # idle JPEG нельзя кэшировать как раньше — теперь зависит от позиции.
        cover_raw = await self._fetch_cover_raw(track)
        artist = ", ".join(track.artists) if track.artists else None
        pos = track_position(self.coordinator)
        duration = float(track.duration_sec) if track.duration_sec else None
        progress = (pos / duration) if (pos is not None and duration and duration > 0) else None
        return await asyncio.to_thread(
            draw_cover_yandex, cover_raw, track.title, artist, progress, pos, duration,
            source_label(track),
        )

    async def _fetch_cover_raw(self, track) -> bytes | None:
        # Каталог Zvuk → CDN по release_id; BT/радио → найденная по title+artist.
        url = cover_url(track) or self.coordinator.current_cover()
        if url is not None:
            if self._cover_cache_url == url and self._cover_raw is not None:
                return self._cover_raw
            raw = await self._download_cover(url)
            if raw is not None:
                self._cover_cache_url = url
                self._cover_raw = raw
                return raw
        # Обложки нет (или скачать не вышло) → CC0-градиент вместо чёрного экрана.
        return fallback_cover(_cover_seed(track))

    async def _download_cover(self, url: str) -> bytes | None:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.coordinator.http_session.get(url, timeout=timeout) as r:
                if r.status == 200:
                    return await r.read()
        except (TimeoutError, aiohttp.ClientError) as exc:
            _LOGGER.debug("cover fetch failed: %s", exc)
        return None


async def _write_jpeg(response: web.StreamResponse, image: bytes) -> None:
    data = (
        b"--frameboundary\r\nContent-Type: image/jpeg\r\nContent-Length: "
        + str(len(image)).encode()
        + b"\r\n\r\n"
        + image
        + b"\r\n"
    )
    # Двойная запись — обход известного бага MJPEG-парсера в Chrome.
    await response.write(data)
    await response.write(data)
