"""Тесты Lrclib.net client + LRC parser.

Реальные кейсы:
- LRC с разной точностью timestamps (.cc vs .ccc)
- current_line на границах (до первой строки, между строками, после последней)
- network error vs 404 — разное поведение для retry-логики
- Пустые track/artist — не делаем запрос
- Fallback на запрос без album/duration если первый дал 404
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from sboom_ha.lyrics_client import (
    Lyrics,
    _parse_lrc,
    current_line,
    fetch_lyrics,
    lyrics_from_dict,
    lyrics_to_dict,
)

# ─────────────────── lyrics_to_dict / lyrics_from_dict ───────────────────


def test_lyrics_roundtrip_synced():
    """Synced lyrics: round-trip сохраняет поля, timeline пересобирается из synced."""
    src = Lyrics(
        plain="line one\nline two",
        synced="[00:01.00]line one\n[00:05.50]line two",
        instrumental=False,
        source="lrclib",
        timeline=_parse_lrc("[00:01.00]line one\n[00:05.50]line two"),
    )
    restored = lyrics_from_dict(lyrics_to_dict(src))
    assert restored.plain == src.plain
    assert restored.synced == src.synced
    assert restored.instrumental is False
    assert restored.source == "lrclib"
    assert restored.timeline == src.timeline  # пересобран из synced


def test_lyrics_roundtrip_plain_only():
    """Без synced — timeline остаётся None."""
    src = Lyrics(plain="just text", synced=None, instrumental=False,
                 source="lrclib", timeline=None)
    restored = lyrics_from_dict(lyrics_to_dict(src))
    assert restored.plain == "just text"
    assert restored.synced is None
    assert restored.timeline is None


def test_lyrics_roundtrip_instrumental():
    src = Lyrics(plain=None, synced=None, instrumental=True,
                 source="lrclib", timeline=None)
    restored = lyrics_from_dict(lyrics_to_dict(src))
    assert restored.instrumental is True
    assert restored.plain is None


def test_lyrics_to_dict_is_json_serialisable():
    """Результат lyrics_to_dict должен быть JSON-совместим (для HA Store)."""
    import json

    d = lyrics_to_dict(Lyrics(plain="x", synced="[00:00.00]x", instrumental=False,
                              source="lrclib", timeline=[(0.0, "x")]))
    json.loads(json.dumps(d))  # не должно бросать


# ─────────────────────────── _parse_lrc ───────────────────────────

def test_parse_lrc_centisecond_format():
    """Стандартный LRC: [MM:SS.cc] (две цифры centisec)."""
    lrc = "[01:23.45]Текст\n[02:00.00]Вторая"
    timeline = _parse_lrc(lrc)
    assert timeline == [(83.45, "Текст"), (120.0, "Вторая")]


def test_parse_lrc_millisecond_format():
    """LRC от Lrclib иногда [MM:SS.ccc] (3 цифры миллисекунды)."""
    lrc = "[01:23.456]With ms"
    timeline = _parse_lrc(lrc)
    assert timeline[0] == (83.456, "With ms")


def test_parse_lrc_sorts_out_of_order_lines():
    """LRC может прийти неотсортированным — проверяем порядок."""
    lrc = "[02:00.00]Вторая\n[00:01.00]Первая\n[01:00.00]Между"
    timeline = _parse_lrc(lrc)
    assert [t for _, t in timeline] == ["Первая", "Между", "Вторая"]


def test_parse_lrc_strips_whitespace_around_text():
    timeline = _parse_lrc("[00:00.00]   текст с пробелами   ")
    assert timeline[0][1] == "текст с пробелами"


def test_parse_lrc_keeps_empty_lines():
    """Пустая строка lyrics (`[01:00.00]`) — это тоже валидный маркер паузы."""
    timeline = _parse_lrc("[00:00.00]Слова\n[00:30.00]\n[01:00.00]Снова")
    assert len(timeline) == 3
    assert timeline[1][1] == ""


def test_parse_lrc_skips_invalid_lines():
    """Не-LRC строки не должны падать или попасть в timeline."""
    lrc = "Random non-LRC text\n[00:05.00]Valid\nAnother junk"
    timeline = _parse_lrc(lrc)
    assert timeline == [(5.0, "Valid")]


def test_parse_lrc_multi_timestamp_line_expands_to_all_points():
    """Регрессия: [00:10.00][01:30.00]Chorus — одна строка на несколько моментов.

    Раньше терялось второе вхождение припева (или строка отбрасывалась целиком)."""
    timeline = _parse_lrc("[00:10.00][01:30.00]Chorus")
    assert timeline == [(10.0, "Chorus"), (90.0, "Chorus")]


def test_parse_lrc_multi_timestamp_sorted_into_timeline():
    """Развёрнутые точки multi-timestamp строки встают по времени среди остальных."""
    lrc = "[00:10.00][01:30.00]Chorus\n[00:20.00]Verse"
    timeline = _parse_lrc(lrc)
    assert timeline == [(10.0, "Chorus"), (20.0, "Verse"), (90.0, "Chorus")]


def test_parse_lrc_strips_word_tags():
    """Регрессия: word-теги enhanced LRC (<00:12.34>) попадали в отрисованный текст."""
    timeline = _parse_lrc("[00:12.00]<00:12.34>Hello <00:13.00>world")
    assert timeline == [(12.0, "Hello world")]


def test_parse_lrc_plain_lines_unchanged_by_multits_support():
    """Обычные однотаймстампные строки работают как раньше."""
    timeline = _parse_lrc("[00:01.00]one\n[00:02.00]two")
    assert timeline == [(1.0, "one"), (2.0, "two")]


def test_parse_lrc_timestamp_mid_line_is_not_a_stamp():
    """Таймстампы учитываются только подряд в начале строки — [..] в середине это текст."""
    timeline = _parse_lrc("[00:05.00]see [00:10.00] marker")
    assert len(timeline) == 1
    assert timeline[0][0] == 5.0
    assert "[00:10.00]" in timeline[0][1]


# ─────────────────────────── current_line ───────────────────────────

def _sample_timeline():
    return [(0.0, "Первая"), (10.0, "Вторая"), (20.0, "Третья"), (30.0, "Финал")]


def test_current_line_returns_none_for_empty_timeline():
    assert current_line(None, 5.0) is None
    assert current_line([], 5.0) is None


def test_current_line_before_first_returns_none():
    """До первой строки — ничего ещё не звучало."""
    assert current_line(_sample_timeline(), -1.0) is None


def test_current_line_at_exact_timestamp_picks_that_line():
    """Если позиция точно на timestamp — это активная строка."""
    assert current_line(_sample_timeline(), 10.0) == "Вторая"
    assert current_line(_sample_timeline(), 20.0) == "Третья"


def test_current_line_between_picks_last_passed():
    """Между строками показываем ту что только что прошла."""
    assert current_line(_sample_timeline(), 15.0) == "Вторая"
    assert current_line(_sample_timeline(), 25.5) == "Третья"


def test_current_line_after_last_picks_last():
    """После финальной строки она остаётся активной."""
    assert current_line(_sample_timeline(), 99999.0) == "Финал"


def test_current_line_skips_empty_marker_lines():
    """Empty-marker (паузы) не должны "стирать" последнюю реплику.

    Это критично: если сейчас inструментал-проигрыш — экран не должен пустеть."""
    timeline = [(0.0, "Слова"), (10.0, ""), (20.0, "Снова")]
    # Между 10 и 20 секунд — был пустой marker, но активная остаётся "Слова"
    assert current_line(timeline, 15.0) == "Слова"


# ─────────────────────────── fetch_lyrics: contracts ───────────────────────────

@pytest.mark.asyncio
async def test_fetch_lyrics_returns_none_immediately_for_empty_track():
    """Без trackname — не дёргаем сеть вообще."""
    session = MagicMock(spec=aiohttp.ClientSession)
    result = await fetch_lyrics(session, "", "Artist")
    assert result is None
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_lyrics_returns_none_immediately_for_empty_artist():
    session = MagicMock(spec=aiohttp.ClientSession)
    assert await fetch_lyrics(session, "Track", "") is None
    session.get.assert_not_called()


def _mock_session_response(status: int, json_data=None):
    """Helper: сессия которая возвращает указанный response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(return_value=ctx)
    return session


@pytest.mark.asyncio
async def test_fetch_lyrics_success_parses_synced_timeline(lrclib_track_response):
    session = _mock_session_response(200, lrclib_track_response)
    result = await fetch_lyrics(session, "Sledgehammer", "Peter Gabriel")
    assert isinstance(result, Lyrics)
    assert result.plain == lrclib_track_response["plainLyrics"]
    assert result.timeline is not None
    assert len(result.timeline) == 4
    assert result.timeline[0] == (11.20, "Hey, hey, you there")


@pytest.mark.asyncio
async def test_fetch_lyrics_404_returns_empty_lyrics_marker():
    """404 — это "не найдено" (валидный результат). НЕ тот же что network error."""
    session = _mock_session_response(404, None)
    result = await fetch_lyrics(session, "Unknown", "Nobody")
    assert isinstance(result, Lyrics)
    assert result.plain is None
    assert result.synced is None
    assert result.timeline is None
    # Источник всё равно зафиксирован, чтобы coordinator знал что не делать retry
    assert result.source == "lrclib"


@pytest.mark.asyncio
async def test_fetch_lyrics_network_error_returns_none_after_retries():
    """Сетевые ошибки — None (caller повторит на следующем track-update)."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
    result = await fetch_lyrics(session, "Track", "Artist", retries=1)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_lyrics_falls_back_to_minimal_query_on_404():
    """Если с album/duration 404 — пробуем без них.

    Реальный кейс: в Lrclib запись часто без album metadata."""
    call_count = 0

    def mk_response(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Первая попытка (с album) — 404
        if call_count == 1:
            resp = MagicMock()
            resp.status = 404
            resp.json = AsyncMock(return_value=None)
        else:
            # Вторая попытка (без album) — успех
            resp = MagicMock()
            resp.status = 200
            resp.json = AsyncMock(return_value={
                "plainLyrics": "found without album",
                "syncedLyrics": None, "instrumental": False,
            })
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=mk_response)

    result = await fetch_lyrics(session, "Track", "Artist", album="WrongAlbum",
                                duration_sec=200)
    assert call_count >= 2, "Должна быть минимум 2 попытки (с album и без)"
    assert isinstance(result, Lyrics)
    assert result.plain == "found without album"


@pytest.mark.asyncio
async def test_fetch_lyrics_instrumental_marked_correctly(lrclib_instrumental_response):
    session = _mock_session_response(200, lrclib_instrumental_response)
    result = await fetch_lyrics(session, "T", "C")
    assert result.instrumental is True
    assert result.plain is None
    assert result.timeline is None


# ─────────────── цепочка провайдеров: LRCLIB → NetEase ───────────────
#
# Регрессии, которые ловят эти тесты: (а) fallback вообще не вызывается;
# (б) сетевой сбой LRCLIB прячет найденный NetEase-результат; (в) NetEase
# игнорирует матчинг по длительности и берёт не тот трек; (г) выключенная
# опция всё равно ходит в NetEase.

def _routed_session(routes):
    """session.get, маршрутизирующий по подстроке URL → (status, json)."""
    calls: list[str] = []

    def mk(url, **kwargs):
        calls.append(url)
        for needle, (status, payload) in routes.items():
            if needle in url:
                resp = MagicMock()
                resp.status = status
                resp.json = AsyncMock(return_value=payload)
                ctx = MagicMock()
                ctx.__aenter__ = AsyncMock(return_value=resp)
                ctx.__aexit__ = AsyncMock(return_value=False)
                return ctx
        raise AssertionError(f"незамоканный URL: {url}")

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=mk)
    return session, calls


_NETEASE_SEARCH_OK = {
    "result": {"songs": [
        {"id": 777, "name": "Track", "duration": 200_000,
         "artists": [{"name": "Artist"}]},
    ]}
}
_NETEASE_LYRIC_OK = {"lrc": {"lyric": "[00:10.00]netease line one\n[00:20.00]netease line two"}}


@pytest.mark.asyncio
async def test_chain_falls_back_to_netease_when_lrclib_not_found():
    """LRCLIB 404 → текст добывается из NetEase (search + lyric)."""
    session, calls = _routed_session({
        "lrclib.net": (404, None),
        "music.163.com/api/search": (200, _NETEASE_SEARCH_OK),
        "music.163.com/api/song/lyric": (200, _NETEASE_LYRIC_OK),
    })
    result = await fetch_lyrics(session, "Track", "Artist", duration_sec=200)
    assert isinstance(result, Lyrics)
    assert result.source == "netease"
    assert result.timeline == [(10.0, "netease line one"), (20.0, "netease line two")]
    assert any("music.163.com" in c for c in calls)


@pytest.mark.asyncio
async def test_chain_returns_netease_even_if_lrclib_network_error():
    """Сетевой сбой LRCLIB не должен прятать найденный NetEase-результат."""
    def mk(url, **kwargs):
        if "lrclib.net" in url:
            raise aiohttp.ClientError("lrclib down")
        routes = {
            "music.163.com/api/search": (200, _NETEASE_SEARCH_OK),
            "music.163.com/api/song/lyric": (200, _NETEASE_LYRIC_OK),
        }
        for needle, (status, payload) in routes.items():
            if needle in url:
                resp = MagicMock()
                resp.status = status
                resp.json = AsyncMock(return_value=payload)
                ctx = MagicMock()
                ctx.__aenter__ = AsyncMock(return_value=resp)
                ctx.__aexit__ = AsyncMock(return_value=False)
                return ctx
        raise AssertionError(url)

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=mk)
    result = await fetch_lyrics(session, "Track", "Artist", retries=0)
    assert result is not None and result.source == "netease"


@pytest.mark.asyncio
async def test_chain_netease_disabled_by_option():
    """use_netease=False: в NetEase не ходим, LRCLIB-404 → маркер «не найдено»."""
    session, calls = _routed_session({"lrclib.net": (404, None)})
    result = await fetch_lyrics(session, "Track", "Artist", use_netease=False)
    assert isinstance(result, Lyrics) and result.plain is None and result.synced is None
    assert not any("music.163.com" in c for c in calls)


@pytest.mark.asyncio
async def test_netease_rejects_song_with_wrong_duration():
    """Матчинг: трек с длительностью вне допуска (±7 c) пропускается —
    иначе на караоке ляжет текст чужой версии (remix/live)."""
    search = {"result": {"songs": [
        {"id": 1, "name": "Track", "duration": 260_000,  # +60 c — мимо
         "artists": [{"name": "Artist"}]},
        {"id": 2, "name": "Track", "duration": 201_000,  # в допуске
         "artists": [{"name": "Artist"}]},
    ]}}
    lyric_calls: list[str] = []

    def mk(url, params=None, **kwargs):
        if "lrclib.net" in url:
            status, payload = 404, None
        elif "api/search" in url:
            status, payload = 200, search
        elif "api/song/lyric" in url:
            lyric_calls.append(str((params or {}).get("id")))
            status, payload = 200, _NETEASE_LYRIC_OK
        else:
            raise AssertionError(url)
        resp = MagicMock()
        resp.status = status
        resp.json = AsyncMock(return_value=payload)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=mk)
    result = await fetch_lyrics(session, "Track", "Artist", duration_sec=200)
    assert result is not None and result.source == "netease"
    assert lyric_calls == ["2"], "должен быть выбран трек с подходящей длительностью"


@pytest.mark.asyncio
async def test_netease_garbage_json_treated_as_not_found():
    """Мусорный ответ NetEase не роняет цепочку — итог «не найдено» (маркер)."""
    session, _ = _routed_session({
        "lrclib.net": (404, None),
        "music.163.com/api/search": (200, {"unexpected": "shape"}),
    })
    result = await fetch_lyrics(session, "Track", "Artist")
    assert isinstance(result, Lyrics) and result.synced is None


@pytest.mark.asyncio
async def test_chain_prefers_netease_synced_over_lrclib_plain():
    """LRCLIB нашёл только plain → NetEase с synced выигрывает (нужен караоке)."""
    session, _ = _routed_session({
        "lrclib.net": (200, {"plainLyrics": "plain only", "syncedLyrics": None,
                             "instrumental": False}),
        "music.163.com/api/search": (200, _NETEASE_SEARCH_OK),
        "music.163.com/api/song/lyric": (200, _NETEASE_LYRIC_OK),
    })
    result = await fetch_lyrics(session, "Track", "Artist", duration_sec=200)
    assert result is not None
    assert result.source == "netease"
    assert result.timeline is not None


@pytest.mark.asyncio
async def test_netease_strips_credit_lines():
    """Реальный кейс (Летов, «Всё идёт по плану»): NetEase отдаёт LRC со
    служебной строкой «[00:00.00] 作曲 : Егор Летов» — кредит композитора,
    а не текст. Без фильтра он висел бы в караоке до первой настоящей строки.
    Фильтруется сам synced (персистится в Store и пересобирается в timeline)."""
    lyric = {"lrc": {"lyric":
        "[00:00.00] 作曲 : Егор Летов\n"
        "[00:12.00]Границы ключ переломлен пополам\n"
        "[00:15.00]А наш батюшка Ленин совсем усоп"}}
    session, _ = _routed_session({
        "lrclib.net": (404, None),
        "music.163.com/api/search": (200, _NETEASE_SEARCH_OK),
        "music.163.com/api/song/lyric": (200, lyric),
    })
    result = await fetch_lyrics(session, "Track", "Artist", duration_sec=200)
    assert result is not None and result.source == "netease"
    assert result.timeline[0] == (12.0, "Границы ключ переломлен пополам")
    assert "作曲" not in (result.synced or "")
    assert "作曲" not in (result.plain or "")
