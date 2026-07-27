"""Тесты PIL-рендера. Главное — не падает на edge-cases.

Реальные сценарии:
- Кадр без cover (cover=None)
- Очень длинный title — должен переноситься, не обрезаться силой
- Кириллица + emoji в lyrics
- Прогресс на границах (0.0, 1.0, нерелевантный 1.5)
"""
from __future__ import annotations

import io

from PIL import Image
from sboom_ha.image_render import (
    HEIGHT,
    WIDTH,
    _blur_bg_cached,
    _karaoke_line_fills,
    _make_blur_bg,
    draw_blank,
    draw_cover_yandex,
    draw_lyrics_with_cover,
    fallback_cover,
    resize_jpeg,
)


def _make_cover(size: int = 50, color=(200, 30, 30)) -> bytes:
    """Маленький настоящий JPEG для теста blur-фона/караоке."""
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="JPEG")
    return buf.getvalue()


def _is_valid_jpeg(data: bytes) -> bool:
    """Открывается через PIL и имеет правильные размеры — значит JPEG валиден."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        return True
    except Exception:
        return False


def _check_dimensions(data: bytes):
    img = Image.open(io.BytesIO(data))
    assert img.size == (WIDTH, HEIGHT)


def test_draw_blank_produces_valid_jpeg():
    out = draw_blank()
    assert _is_valid_jpeg(out)
    _check_dimensions(out)


def test_draw_lyrics_without_cover_renders_dark_background():
    """Когда обложка не загрузилась — должен быть рендер на тёмном фоне, без exception."""
    out = draw_lyrics_with_cover(
        cover=None, current="Текст", next_line="следующая",
        title="T", artist="A",
    )
    assert _is_valid_jpeg(out)
    _check_dimensions(out)


def test_draw_lyrics_with_invalid_cover_bytes_falls_back():
    """Если получили битый cover (например, 404 HTML вместо JPEG) — fallback на тёмный фон."""
    out = draw_lyrics_with_cover(
        cover=b"not a real image",
        current="line", next_line=None,
        title="T", artist="A",
    )
    assert _is_valid_jpeg(out), "Битый cover не должен ломать рендер"


def test_draw_lyrics_handles_cyrillic_and_emoji():
    """В lyrics могут быть Юникод-символы. PIL+DejaVuSans должен их рендерить."""
    out = draw_lyrics_with_cover(
        cover=None,
        current="Привет мир 🎵 это тест",
        next_line="Ещё одна строка с словами",
        title="Тест", artist="Артист",
    )
    assert _is_valid_jpeg(out)


def test_draw_lyrics_handles_very_long_line():
    """Длинная строка должна переноситься, не отрезаться.

    Реальный кейс: некоторые lyrics длиной 60+ символов в одной строке."""
    long_line = "x" * 200  # очень длинная строка
    out = draw_lyrics_with_cover(
        cover=None, current=long_line, next_line=None,
        title="T", artist="A",
    )
    # Главное — не упало. Что внутри картинки — проверяется визуально.
    assert _is_valid_jpeg(out)


def test_draw_lyrics_with_progress_at_boundaries():
    """Прогресс на 0%, 100%, и аномальные значения."""
    for p in [0.0, 0.5, 1.0, -0.1, 1.5]:
        out = draw_lyrics_with_cover(
            cover=None, current="x", next_line="y",
            title="T", artist="A",
            progress=p, position_sec=10, duration_sec=200,
        )
        assert _is_valid_jpeg(out), f"Failed at progress={p}"


def test_draw_cover_yandex_without_position_data():
    """Когда нет позиции/длительности (live-stream) — рендерим без time-метки."""
    out = draw_cover_yandex(
        cover=None, title="LiveStream", artist="DJ",
        progress=None, position_sec=None, duration_sec=None,
    )
    assert _is_valid_jpeg(out)


def test_draw_cover_yandex_handles_all_none():
    """Полностью пустой track (только что подключились) — тоже рендеримый кадр."""
    out = draw_cover_yandex(cover=None, title=None, artist=None)
    assert _is_valid_jpeg(out)


# ─────────────────────── караоке-заливка (line_progress) ───────────────────────

def test_karaoke_fill_actually_changes_frame():
    """Регрессия: заливка пропетой части должна реально рисоваться.

    Кадр с line_progress=0.5 обязан отличаться и от статичного (None),
    и от заливки в нуле (0.0) — иначе караоке-режим визуально мёртв."""
    cover = _make_cover()
    kwargs = dict(
        cover=cover, current="Karaoke test line here", next_line="next line",
        title="T", artist="A", progress=0.3, position_sec=30.0, duration_sec=100.0,
    )
    half = draw_lyrics_with_cover(line_progress=0.5, **kwargs)
    static = draw_lyrics_with_cover(line_progress=None, **kwargs)
    zero = draw_lyrics_with_cover(line_progress=0.0, **kwargs)
    assert _is_valid_jpeg(half)
    assert half != static, "line_progress=0.5 не отличился от статичного кадра"
    assert half != zero, "line_progress=0.5 не отличился от заливки 0.0"


# ─────────────────────── blur-фон: кэш ───────────────────────

def test_blur_bg_cached_returns_same_object_for_equal_bytes():
    """Кэш по значению bytes: два равных (но разных) объекта → один Image."""
    cover = _make_cover(color=(10, 120, 40))
    a = _blur_bg_cached(cover)
    b = _blur_bg_cached(bytes(cover))  # равный, но другой объект bytes
    assert a is b, "lru_cache должен отдавать один и тот же Image для одинаковой обложки"


def test_make_blur_bg_returns_fresh_copies():
    """Регрессия: кэшированный Image нельзя отдавать под ImageDraw —
    рисование мутировало бы кэш и «пачкало» все последующие кадры."""
    cover = _make_cover(color=(40, 40, 200))
    cached = _blur_bg_cached(cover)
    c1 = _make_blur_bg(cover)
    c2 = _make_blur_bg(cover)
    assert c1 is not c2
    assert c1 is not cached and c2 is not cached
    # копия эквивалентна кэшу по содержимому
    assert c1.tobytes() == cached.tobytes()


# ─────────────────────── resize_jpeg ───────────────────────

def test_resize_jpeg_shrinks_to_requested_width():
    out = resize_jpeg(draw_blank(), 320, None)
    img = Image.open(io.BytesIO(out))
    assert img.size == (320, 180)  # aspect 16:9 сохранён


def test_resize_jpeg_shrinks_to_requested_height():
    out = resize_jpeg(draw_blank(), None, 90)
    img = Image.open(io.BytesIO(out))
    assert img.size == (160, 90)


def test_resize_jpeg_noop_without_dimensions():
    src = draw_blank()
    assert resize_jpeg(src, None, None) is src


# ─────────── посимвольная караоке-заливка (многострочные тексты) ───────────
#
# Регрессия: единый вертикальный срез по всему боксу красил многострочный
# текст на всех экранных строках одновременно. Теперь закраска идёт в порядке
# чтения: прогресс распределяется по символам, слово получает время
# пропорционально длине.



def test_karaoke_fills_sequential_lines():
    """Прогресс 0.75 двух равных строк: первая целиком, вторая наполовину."""
    assert _karaoke_line_fills(["aaaaa", "aaaaa"], 0.75) == [1.0, 0.5]


def test_karaoke_fills_boundary_and_edges():
    assert _karaoke_line_fills(["aaaaa", "aaaaa"], 0.5) == [1.0, 0.0]
    assert _karaoke_line_fills(["aaaaa", "aaaaa"], 0.0) == [0.0, 0.0]
    assert _karaoke_line_fills(["aaaaa", "aaaaa"], 1.0) == [1.0, 1.0]


def test_karaoke_fills_weighted_by_length():
    """Слова/строки получают время пропорционально длине: 8 символов из 10 —
    это вся первая строка (8) и ничего от второй (2)."""
    assert _karaoke_line_fills(["aaaaaaaa", "aa"], 0.8) == [1.0, 0.0]
    fills = _karaoke_line_fills(["aaaaaaaa", "aa"], 0.9)
    assert fills[0] == 1.0 and abs(fills[1] - 0.5) < 1e-9


def test_karaoke_fills_empty_text_safe():
    assert _karaoke_line_fills([], 0.5) == []
    assert _karaoke_line_fills([""], 0.5) == [0.0]


def _accent_present(img, y_from, y_to):
    """Есть ли в горизонтальной полосе пиксели караоке-акцента (255,193,71)
    с допуском на JPEG-сжатие."""
    region = img.crop((0, y_from, img.width, y_to))
    for r, g, b in region.getdata():
        if r > 200 and 150 < g < 235 and b < 140:
            return True
    return False


def test_karaoke_multiline_paints_first_screen_line_only():
    """Прогресс 0.5 у текста из двух экранных строк: акцент есть в полосе
    первой строки и ОТСУТСТВУЕТ в полосе второй. Старый рендер (общий срез)
    красил обе полосы одновременно — этот тест его убивает."""
    import io

    from PIL import Image
    from sboom_ha.image_render import HEIGHT, draw_lyrics_with_cover

    # Два «слова» по 20 символов → wrap на две экранные строки (line_width=22)
    text = "а" * 20 + " " + "б" * 20
    jpeg = draw_lyrics_with_cover(None, text, None, None, None, line_progress=0.5)
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")

    # Геометрия из draw_lyrics_with_cover: box=(40, H/6, W-80, H/3), font 90,
    # 2 строки → y0 = 120 + (240 - 180)//2 = 150; полосы строк: 150..240, 240..330.
    assert HEIGHT == 720, "геометрия теста рассчитана под 720p"
    assert _accent_present(img, 155, 235), "первая экранная строка должна быть закрашена"
    assert not _accent_present(img, 245, 330), "вторая строка НЕ должна быть закрашена при 0.5"


def test_karaoke_multiline_full_progress_paints_both_lines():
    import io

    from PIL import Image
    from sboom_ha.image_render import draw_lyrics_with_cover

    text = "а" * 20 + " " + "б" * 20
    jpeg = draw_lyrics_with_cover(None, text, None, None, None, line_progress=1.0)
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert _accent_present(img, 155, 235)
    assert _accent_present(img, 245, 330)


def _white_present(img, y_from, y_to):
    region = img.crop((0, y_from, img.width, y_to))
    return any(r > 230 and g > 230 and b > 230 for r, g, b in region.getdata())


def test_karaoke_partial_line_has_both_colors():
    """Частично пропетая строка содержит И акцентные, И белые символы.

    Ловит регрессию прямой отрисовки: если префикс перекрасит всю строку
    (или белый слой не нарисуется), одна из проверок упадёт."""
    import io

    from PIL import Image
    from sboom_ha.image_render import draw_lyrics_with_cover

    # Одна экранная строка (18 символов < line_width 22), прогресс 0.5
    text = "ы" * 18
    jpeg = draw_lyrics_with_cover(None, text, None, None, None, line_progress=0.5)
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    # Одна строка: y0 = 120 + (240-90)//2 = 195; полоса 195..285
    assert _accent_present(img, 195, 285), "пропетая часть должна быть акцентной"
    assert _white_present(img, 195, 285), "непропетая часть должна остаться белой"


def test_source_label_helper():
    """helpers.source_label собирает «Плейлист · Провайдер», опуская пустое."""
    from types import SimpleNamespace

    from sboom_ha.helpers import provider_label, source_label
    assert provider_label("zvuk") == "Sber Звук"
    assert provider_label("unknown_x") == "unknown_x"
    assert provider_label(None) is None
    t = SimpleNamespace(playlist_title="Персональная волна", provider="zvuk")
    assert source_label(t) == "Персональная волна · Sber Звук"
    assert source_label(SimpleNamespace(playlist_title=None, provider="zvuk")) == "Sber Звук"
    assert source_label(SimpleNamespace(playlist_title=None, provider=None)) is None
    assert source_label(None) is None


def test_karaoke_frame_renders_source_label():
    """Плашка источника реально появляется на кадре: кадр с source отличается
    байтами от кадра без него (текст нарисован в верхней полосе)."""
    import io

    from PIL import Image
    from sboom_ha.image_render import draw_lyrics_with_cover

    base = draw_lyrics_with_cover(None, "строка", None, "T", "A")
    withsrc = draw_lyrics_with_cover(None, "строка", None, "T", "A", source="Волна · Sber Звук")
    assert base != withsrc
    # в верхней полосе (плашка ~y=24..64) появляются светлые пиксели текста
    img = Image.open(io.BytesIO(withsrc)).convert("RGB")
    band = img.crop((0, 20, img.width, 70))
    assert any(r > 140 and g > 140 and b > 140 for r, g, b in band.getdata())


# ─────────────── fallback_cover (заглушка вместо чёрного экрана) ───────────────

def test_fallback_cover_returns_valid_bundled_jpeg():
    """Заглушка отдаёт валидный JPEG из бандла (а не чёрный/пусто)."""
    raw = fallback_cover("Artist|Song")
    assert raw is not None
    img = Image.open(io.BytesIO(raw))
    assert img.format == "JPEG"
    assert img.size[0] > 0


def test_fallback_cover_stable_per_seed():
    """Один и тот же seed → тот же фон (иначе фон мигал бы от кадра к кадру)."""
    assert fallback_cover("Bonnie Tyler|Spell") == fallback_cover("Bonnie Tyler|Spell")


def test_fallback_cover_varies_across_seeds():
    """Разные треки распределяются по набору (хотя бы 2 разных фона на выборке)."""
    seeds = [f"artist{i}|track{i}" for i in range(30)]
    chosen = {fallback_cover(s) for s in seeds}
    assert len(chosen) >= 2  # не все одинаковые — выбор реально зависит от seed


def test_fallback_cover_renders_non_black_background():
    """Кадр с заглушкой — не чёрный (есть цветной градиент под текстом)."""
    frame = draw_lyrics_with_cover(
        fallback_cover("X|Y"), "line", None, "Title", "Artist",
    )
    img = Image.open(io.BytesIO(frame)).convert("RGB")
    # хоть один пиксель заметно цветной/светлее near-black (15,15,18)
    assert any(sum(px) > 90 for px in img.getdata())
