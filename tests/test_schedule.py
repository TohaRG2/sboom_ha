"""Тесты разбора расписания колонки (будильники/таймеры/напоминания).

Основаны на РЕАЛЬНОМ захвате GET_STATE (tests/fixtures/alarm_state.json):
- будильник «каждый день» 05:00:03 UTC (RRULE BYDAY=MO..SU),
- будильник «будни» 04:00:40 UTC (BYDAY=MO..FR),
- таймер «2 часа», осталось 6489 c,
- напоминание «встреча» на 2026-07-10T11:48:00Z.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sboom_ha._schedule import (
    ScheduleEvent,
    alarm_events,
    next_alarm,
    next_timer,
    reminder_events,
    timer_events,
)

_STATE = json.loads((Path(__file__).parent / "fixtures" / "alarm_state.json").read_text())
_ALARMS = _STATE["alarm"]["alarms"]
_TIMERS = _STATE["alarm"]["timers"]
_REMINDERS = _STATE["reminders"]


# ─────────────────── будильники (iCalendar RRULE) ───────────────────

def test_alarm_events_expand_daily_and_weekday():
    """Оба будильника раскрываются в окне; для «будни» суббота/воскресенье
    пропущены (проверяем, что RRULE BYDAY реально применяется)."""
    now = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)  # понедельник
    events = alarm_events(_ALARMS, now, horizon=timedelta(days=7))
    # неделя: ежедневный даёт 7, будничный — 5 (Пн–Пт) → 12 срабатываний
    assert len(events) == 12
    daily = sorted(e.start for e in events if e.start.hour == 5)
    weekday = sorted(e.start for e in events if e.start.hour == 4)
    assert len(daily) == 7
    assert len(weekday) == 5
    # у будничного нет срабатываний в Сб(18)/Вс(19)
    assert all(e.weekday() < 5 for e in weekday)
    assert all(e.category == "alarm" for e in events)


def test_next_alarm_is_earliest_future():
    """next_alarm = ближайшее будущее срабатывание среди всех будильников."""
    now = datetime(2026, 7, 13, 4, 30, tzinfo=UTC)  # Пн, между 04:00 и 05:00
    nxt = next_alarm(_ALARMS, now)
    # 04:00:40 уже прошло → следующее ежедневное 05:00:03 того же дня
    assert nxt == datetime(2026, 7, 13, 5, 0, 3, tzinfo=UTC)


def test_disabled_alarm_skipped():
    disabled = [{**_ALARMS[0], "enabled": False}]
    assert alarm_events(disabled, datetime(2026, 7, 13, tzinfo=UTC)) == []
    assert next_alarm(disabled, datetime(2026, 7, 13, tzinfo=UTC)) is None


def test_alarm_summary_falls_back_to_default():
    """Пустой description → человекочитаемое имя, а не пустая строка."""
    now = datetime(2026, 7, 13, tzinfo=UTC)
    ev = alarm_events(_ALARMS, now, horizon=timedelta(days=1))[0]
    assert ev.summary == "Будильник"


def test_alarm_one_shot_without_rrule():
    """Будильник без RRULE — одноразовое срабатывание в окне."""
    one = [{
        "id": "x", "enabled": True, "description": "разовый",
        "ics": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20260715T060000Z\r\nUID:x\r\nEND:VEVENT\r\nEND:VCALENDAR",
    }]
    now = datetime(2026, 7, 14, tzinfo=UTC)
    events = alarm_events(one, now, horizon=timedelta(days=7))
    assert len(events) == 1
    assert events[0].start == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)
    # вне окна — пусто
    assert alarm_events(one, datetime(2026, 7, 16, tzinfo=UTC)) == []


# ─────────────────── таймеры ───────────────────

def test_timer_events_absolute_end_from_timeleft():
    now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    ev = timer_events(_TIMERS, now)[0]
    assert ev.end == now + timedelta(seconds=6489)
    # начало = конец − полная длительность (2 ч)
    assert ev.start == ev.end - timedelta(seconds=7200)
    assert ev.summary == "2 часа"
    assert ev.category == "timer"


def test_next_timer_earliest_end():
    now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    assert next_timer(_TIMERS, now) == now + timedelta(seconds=6489)
    assert next_timer([], now) is None


# ─────────────────── напоминания ───────────────────

def test_reminder_events_from_time_reminders():
    events = reminder_events(_REMINDERS)
    assert len(events) == 1
    ev = events[0]
    assert ev.summary == "встреча"
    assert ev.start == datetime(2026, 7, 10, 11, 48, tzinfo=UTC)
    assert ev.category == "reminder"


def test_reminder_with_subtitle_combined():
    block = {"reminders": {"time_reminders": {"k": [
        {"id": "r", "title": "Позвонить", "subtitle": "маме",
         "reminderTime": "2026-07-10T09:00:00.000Z"}
    ]}}}
    assert reminder_events(block)[0].summary == "Позвонить — маме"


def test_reminder_empty_block_safe():
    assert reminder_events({}) == []
    assert reminder_events({"reminders": {}}) == []


def test_schedule_event_is_frozen():
    """ScheduleEvent — value object, не мутируется случайно."""
    ev = ScheduleEvent("u", "s", datetime(2026, 1, 1, tzinfo=UTC),
                        datetime(2026, 1, 1, tzinfo=UTC), "alarm")
    import pytest
    with pytest.raises((AttributeError, TypeError)):
        ev.summary = "x"  # type: ignore[misc]
