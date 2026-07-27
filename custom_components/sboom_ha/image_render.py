"""Рендер обложек и lyrics-кадров для camera-entity (HD 1280x720)."""
from __future__ import annotations

import io
import os
import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

WIDTH = 1280
HEIGHT = 720
WIDTH2 = WIDTH // 2
HEIGHT2 = HEIGHT // 2
HEIGHT6 = HEIGHT // 6
COVER_BOX = 400  # ширина/высота квадратной обложки на canvas

# Цвет «пропетой» части строки в караоке-режиме.
KARAOKE_ACCENT = (255, 193, 71)

_FONT_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fonts", "DejaVuSans.ttf")


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # Кэш: без него шрифт читался с диска на каждый вызов _draw_text —
    # заметная часть стоимости кадра при 5 FPS.
    return ImageFont.truetype(_FONT_PATH, size, encoding="UTF-8")


def _layout_text(
    text: str,
    box: tuple[int, int, int, int],
    anchor: str,
    font_size: int,
    line_width: int,
) -> tuple[list[str], int, str, int, int]:
    """Разбиение текста на экранные строки + геометрия отрисовки.

    Возвращает (lines, x, align, y0, font_size) — единый источник разметки
    для обычного рендера и караоке-заливки (иначе они разъезжаются на
    многострочных текстах). Логика уменьшения шрифта при переполнении —
    исторически из _draw_text.
    """
    lines = re.findall(rf"(.{{1,{line_width}}})(?:\s|$)", text)
    if (font_size > 70 and len(lines) > 3) or (font_size <= 70 and len(lines) > 4):
        return _layout_text(text, box, anchor, font_size - 10, line_width + 3)

    if anchor[0] == "l":
        x, align = box[0], "la"
    elif anchor[0] == "m":
        x, align = box[0] + box[2] // 2, "ma"
    elif anchor[0] == "r":
        x, align = box[0] + box[2], "ra"
    else:
        raise ValueError(anchor)

    if anchor[1] == "t":
        y = box[1]
    elif anchor[1] == "m":
        y = box[1] + (box[3] - len(lines) * font_size) // 2
    elif anchor[1] == "b":
        y = box[1] + (box[3] - len(lines) * font_size)
    else:
        raise ValueError(anchor)

    return lines, x, align, y, font_size


def _draw_text(
    ctx: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    anchor: str,
    fill,
    font_size: int,
    line_width: int = 20,
) -> None:
    """Многострочный текст с автопереносом и smart-anchor."""
    lines, x, align, y, font_size = _layout_text(text, box, anchor, font_size, line_width)
    font = _font(font_size)
    for line in lines:
        ctx.text((x, y), line, anchor=align, fill=fill, font=font)
        y += font_size


def _karaoke_line_fills(lines: list[str], frac: float) -> list[float]:
    """Доля закраски каждой экранной строки при общем прогрессе frac (0..1).

    Прогресс распределяется ПО СИМВОЛАМ всего текста в порядке чтения:
    сначала полностью закрашивается первая экранная строка, затем вторая
    и т.д. Слово автоматически получает время пропорционально своей длине —
    длинные слова «поются» дольше коротких.
    """
    total = sum(len(line) for line in lines)
    if total == 0:
        return [0.0] * len(lines)
    done = max(0.0, min(1.0, frac)) * total
    fills: list[float] = []
    for line in lines:
        if not line or done <= 0:
            fills.append(0.0)
        elif done >= len(line):
            fills.append(1.0)
            done -= len(line)
        else:
            fills.append(done / len(line))
            done = 0.0
    return fills


def draw_cover(title: str | None, artist: str | None, cover: bytes | None) -> bytes:
    """Cover (опц.) + title + artist на чёрном фоне."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    if cover:
        try:
            img = Image.open(io.BytesIO(cover)).convert("RGB")
            img = img.resize((COVER_BOX, COVER_BOX))
            canvas.paste(img, (WIDTH2 - COVER_BOX // 2, HEIGHT6 * 2 - COVER_BOX // 2))
        except Exception:
            pass
    ctx = ImageDraw.Draw(canvas)
    if title:
        _draw_text(ctx, title, (0, HEIGHT6 * 4, WIDTH, HEIGHT6), "mb", "white", 60, 35)
    if artist:
        _draw_text(ctx, artist, (0, HEIGHT6 * 5, WIDTH, HEIGHT6), "mt", "grey", 50, 40)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def draw_lyrics(first: str | None, second: str | None) -> bytes:
    """Текущая (большая, белая) + следующая (меньше, серая) строки."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    ctx = ImageDraw.Draw(canvas)
    if first:
        _draw_text(ctx, first, (0, 50, WIDTH, HEIGHT2 - 50), "mm", "white", 100)
    if second:
        _draw_text(ctx, second, (0, HEIGHT2, WIDTH, HEIGHT2 - 50), "mm", "grey", 100)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def draw_blank() -> bytes:
    """Серый кадр-заглушка."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "grey")
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def resize_jpeg(jpeg: bytes, width: int | None, height: int | None) -> bytes:
    """Уменьшить JPEG под запрошенный HA размер (aspect сохраняется)."""
    if not width and not height:
        return jpeg
    try:
        img = Image.open(io.BytesIO(jpeg))
        img.thumbnail((width or img.width, height or img.height), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return jpeg


_BG_DIR = os.path.join(os.path.dirname(__file__), "backgrounds")


@lru_cache(maxsize=1)
def _fallback_bgs() -> tuple[bytes, ...]:
    """Бандл свободных градиентов (StockSnap/Flickr, CC0 1.0) — фон когда обложки нет."""
    if not os.path.isdir(_BG_DIR):
        return ()
    files = sorted(f for f in os.listdir(_BG_DIR) if f.endswith(".jpg"))
    out = []
    for name in files:
        try:
            with open(os.path.join(_BG_DIR, name), "rb") as fh:
                out.append(fh.read())
        except OSError:
            continue
    return tuple(out)


def fallback_cover(seed: str | None) -> bytes | None:
    """Заглушка-фон: стабильно-случайный градиент по seed (одному треку — один фон).

    Вместо чёрного экрана, когда обложка не нашлась (BT/радио или редкий трек).
    Выбор детерминирован по seed → фон не мигает от кадра к кадру, но у разных
    треков разный.
    """
    bgs = _fallback_bgs()
    if not bgs:
        return None
    idx = (sum(seed.encode("utf-8", "ignore")) if seed else 0) % len(bgs)
    return bgs[idx]


@lru_cache(maxsize=4)
def _blur_bg_cached(cover: bytes | None) -> Image.Image:
    """Cover→fill→blur→darken — фон в стиле Яндекс.Музыки.

    Кэш по байтам обложки: fit(LANCZOS) + GaussianBlur(24) на 1280×720 —
    десятки миллисекунд CPU, а фон меняется только со сменой обложки.
    Без кэша это главный барьер для повышения FPS караоке-стрима.
    """
    if not cover:
        return Image.new("RGB", (WIDTH, HEIGHT), (15, 15, 18))
    try:
        img = Image.open(io.BytesIO(cover)).convert("RGB")
        # cover-fit с обрезкой до полного canvas
        img = ImageOps.fit(img, (WIDTH, HEIGHT), Image.LANCZOS)
        img = img.filter(ImageFilter.GaussianBlur(radius=24))
        # затемнение через смешение с чёрным
        dark = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        img = Image.blend(img, dark, 0.55)
        return img
    except Exception:
        return Image.new("RGB", (WIDTH, HEIGHT), (15, 15, 18))


def _make_blur_bg(cover: bytes | None) -> Image.Image:
    # .copy() обязателен: кэшированный Image нельзя отдавать под ImageDraw.
    return _blur_bg_cached(cover).copy()


def _draw_text_karaoke(
    canvas: Image.Image,
    text: str,
    box: tuple[int, int, int, int],
    font_size: int,
    line_width: int,
    frac: float,
) -> None:
    """Строка с «заливкой» по мере пропевания (псевдо-караоке).

    Word-level тайминг из lrclib недоступен (line-level LRC), поэтому доля
    прохождения строки — линейная интерполяция между таймстампами соседних
    строк. Закраска распределяется по символам текста в порядке чтения
    (через переносы): слово получает время пропорционально длине, экранные
    строки закрашиваются последовательно, а не одновременно.
    """
    frac = max(0.0, min(1.0, frac))
    # Прямая отрисовка текстом, без полнокадровых масок: белая строка
    # целиком + «пропетый» префикс акцентным цветом ПОВЕРХ, тем же шрифтом
    # от того же левого края. Позиции глифов префикса совпадают с полной
    # строкой пиксель-в-пиксель (кернинг зависит только от пар внутри
    # префикса), поэтому символы не плывут. Гранулярность — символ:
    # буква загорается целиком, без разрезанных глифов, как в классическом
    # караоке. Дешевле маскирования на ~4 полнокадровых буфера на кадр.
    lines, x, _align, y0, font_size = _layout_text(text, box, "mm", font_size, line_width)
    font = _font(font_size)
    ctx = ImageDraw.Draw(canvas)
    y = y0
    for line, fill_frac in zip(lines, _karaoke_line_fills(lines, frac), strict=True):
        # Пропетая часть строки — целое число символов (floor: символ
        # «загорается», когда пропет).
        sung_chars = int(fill_frac * len(line)) if fill_frac < 1.0 else len(line)
        sung = line[:sung_chars]
        unsung = line[sung_chars:]

        if unsung:
            ctx.text((x, y), line, anchor="ma", fill="white", font=font)
        if sung:
            # Левый край центрированной строки: центр − ширина/2.
            x0 = x - font.getlength(line) / 2
            ctx.text((x0, y), sung, anchor="la", fill=KARAOKE_ACCENT, font=font)
        y += font_size


def _draw_progress(ctx: ImageDraw.ImageDraw, progress: float | None) -> None:
    """Тонкая линия прогресса по нижней кромке canvas (0.0..1.0)."""
    if progress is None:
        return
    p = max(0.0, min(1.0, progress))
    bar_y = HEIGHT - 8
    # фон-полоска (приглушённая)
    ctx.rectangle((40, bar_y, WIDTH - 40, bar_y + 4), fill=(60, 60, 65))
    # активная часть — белая
    end_x = 40 + int((WIDTH - 80) * p)
    if end_x > 40:
        ctx.rectangle((40, bar_y, end_x, bar_y + 4), fill=(245, 245, 245))


def _format_time(sec: float | None) -> str:
    if sec is None or sec < 0:
        return "--:--"
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


def draw_lyrics_with_cover(
    cover: bytes | None,
    current: str | None,
    next_line: str | None,
    title: str | None,
    artist: str | None,
    progress: float | None = None,
    position_sec: float | None = None,
    duration_sec: float | None = None,
    line_progress: float | None = None,
    source: str | None = None,
) -> bytes:
    """Яндекс-стиль: blur-обложка + lyrics поверх + футер с title/artist + progress.

    line_progress (0..1) — доля пропетости текущей строки: включает
    караоке-заливку (см. _draw_text_karaoke). None — статичный белый текст.
    source — плашка контекста вверху («Персональная волна · Sber Звук»).
    """
    canvas = _make_blur_bg(cover)
    ctx = ImageDraw.Draw(canvas)

    # Плашка источника вверху по центру (приглушённая, малым шрифтом).
    if source:
        _draw_text(ctx, source, (0, 24, WIDTH, 40), "mt", (170, 170, 175), 28, line_width=60)

    # Lyrics верх — две строки в верхних 2/3 экрана.
    if current:
        cur_box = (40, HEIGHT // 6, WIDTH - 80, HEIGHT // 3)
        if line_progress is not None:
            _draw_text_karaoke(canvas, current, cur_box, 90, 22, line_progress)
        else:
            _draw_text(ctx, current, cur_box, "mm", "white", 90, line_width=22)
    if next_line:
        _draw_text(
            ctx, next_line,
            (40, HEIGHT // 2 + 20, WIDTH - 80, HEIGHT // 4),
            "mm", (210, 210, 210), 70, line_width=26,
        )

    # Footer: title + artist + время позиции/длительности.
    if title:
        _draw_text(
            ctx, title,
            (0, HEIGHT - 165, WIDTH, 60),
            "mb", "white", 46, line_width=40,
        )
    if artist:
        _draw_text(
            ctx, artist,
            (0, HEIGHT - 95, WIDTH, 40),
            "mt", (190, 190, 190), 32, line_width=50,
        )
    # время слева/справа над progress-bar
    if position_sec is not None or duration_sec is not None:
        font = _font(22)
        ctx.text((40, HEIGHT - 38), _format_time(position_sec), font=font, fill=(220, 220, 220))
        ctx.text((WIDTH - 40, HEIGHT - 38), _format_time(duration_sec),
                 anchor="ra", font=font, fill=(220, 220, 220))
    _draw_progress(ctx, progress)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def draw_cover_yandex(
    cover: bytes | None,
    title: str | None,
    artist: str | None,
    progress: float | None = None,
    position_sec: float | None = None,
    duration_sec: float | None = None,
    source: str | None = None,
) -> bytes:
    """Idle-режим (lyrics нет): blur-фон + большая обложка по центру + подписи."""
    canvas = _make_blur_bg(cover)
    if cover:
        try:
            img = Image.open(io.BytesIO(cover)).convert("RGB")
            box = 360
            img = img.resize((box, box), Image.LANCZOS)
            canvas.paste(img, (WIDTH2 - box // 2, HEIGHT // 2 - box // 2 - 50))
        except Exception:
            pass
    ctx = ImageDraw.Draw(canvas)
    if source:
        _draw_text(ctx, source, (0, 24, WIDTH, 40), "mt", (170, 170, 175), 28, line_width=60)
    if title:
        _draw_text(ctx, title, (0, HEIGHT - 165, WIDTH, 60), "mb", "white", 50, 35)
    if artist:
        _draw_text(ctx, artist, (0, HEIGHT - 95, WIDTH, 40), "mt", (190, 190, 190), 36, 45)
    if position_sec is not None or duration_sec is not None:
        font = _font(22)
        ctx.text((40, HEIGHT - 38), _format_time(position_sec), font=font, fill=(220, 220, 220))
        ctx.text((WIDTH - 40, HEIGHT - 38), _format_time(duration_sec),
                 anchor="ra", font=font, fill=(220, 220, 220))
    _draw_progress(ctx, progress)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=80)
    return buf.getvalue()
