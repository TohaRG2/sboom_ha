"""Разбор расписания колонки: будильники / таймеры / напоминания → события.

Данные приходят в GET_STATE (op=12): alarm.alarms[] (iCalendar RRULE),
alarm.timers[] (обратный отсчёт), reminders.reminders.time_reminders (по ISO).
Модуль чистый (без HA) — используется и календарём, и сенсорами.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.rrule import rrulestr

_LOGGER = logging.getLogger(__name__)

# На сколько вперёд раскрываем повторяющиеся будильники для календаря.
DEFAULT_HORIZON = timedelta(days=90)


@dataclass(frozen=True)
class ScheduleEvent:
    """Одно событие расписания (в UTC)."""

    uid: str
    summary: str
    start: datetime
    end: datetime
    category: str  # "alarm" | "timer" | "reminder"


def _parse_dtstart(ics: str) -> datetime | None:
    """DTSTART из ics → aware UTC datetime (формат 20260711T050003Z)."""
    for line in ics.replace("\r\n", "\n").split("\n"):
        if line.startswith("DTSTART"):
            value = line.split(":", 1)[-1].strip()
            try:
                return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            except ValueError:
                return None
    return None


def _alarm_occurrences(
    ics: str, start: datetime, end: datetime
) -> list[datetime]:
    """Срабатывания будильника в окне [start, end] по DTSTART+RRULE.

    Одноразовый будильник (без RRULE) даёт одно срабатывание, если DTSTART
    попал в окно.
    """
    lines = [
        ln
        for ln in ics.replace("\r\n", "\n").split("\n")
        if ln.startswith(("DTSTART", "RRULE"))
    ]
    dtstart = _parse_dtstart(ics)
    if dtstart is None:
        return []
    if not any(ln.startswith("RRULE") for ln in lines):
        return [dtstart] if start <= dtstart <= end else []
    try:
        rule = rrulestr("\n".join(lines))
        return list(rule.between(start, end, inc=True))
    except (ValueError, TypeError) as exc:
        _LOGGER.debug("bad alarm RRULE %r: %s", ics[:80], exc)
        return []


def alarm_events(
    alarms: list[dict[str, Any]],
    now: datetime,
    horizon: timedelta = DEFAULT_HORIZON,
) -> list[ScheduleEvent]:
    """Будильники → события в окне [now, now+horizon]. Отключённые пропускаем."""
    end = now + horizon
    out: list[ScheduleEvent] = []
    for a in alarms:
        if not isinstance(a, dict) or not a.get("enabled", True):
            continue
        ics = a.get("ics")
        if not isinstance(ics, str):
            continue
        summary = a.get("description") or "Будильник"
        uid = str(a.get("id") or "")
        for occ in _alarm_occurrences(ics, now, end):
            out.append(
                ScheduleEvent(
                    uid=f"alarm-{uid}-{occ.isoformat()}",
                    summary=summary,
                    start=occ,
                    end=occ + timedelta(minutes=1),
                    category="alarm",
                )
            )
    return out


def timer_events(timers: list[dict[str, Any]], now: datetime) -> list[ScheduleEvent]:
    """Таймеры → события: конец = now + timeLeftSec, начало = конец − intervalSec."""
    out: list[ScheduleEvent] = []
    for t in timers:
        if not isinstance(t, dict):
            continue
        left = t.get("timeLeftSec")
        if not isinstance(left, (int, float)):
            continue
        fire = now + timedelta(seconds=left)
        interval = t.get("intervalSec")
        start = (
            fire - timedelta(seconds=interval)
            if isinstance(interval, (int, float))
            else fire
        )
        out.append(
            ScheduleEvent(
                uid=f"timer-{t.get('id') or ''}",
                summary=t.get("description") or "Таймер",
                start=start,
                end=fire,
                category="timer",
            )
        )
    return out


def reminder_events(reminders_block: dict[str, Any]) -> list[ScheduleEvent]:
    """Напоминания (reminders.reminders.time_reminders) → события."""
    out: list[ScheduleEvent] = []
    time_reminders = (
        (reminders_block or {}).get("reminders", {}).get("time_reminders", {})
    )
    if not isinstance(time_reminders, dict):
        return out
    for items in time_reminders.values():
        if not isinstance(items, list):
            continue
        for r in items:
            if not isinstance(r, dict):
                continue
            when = _parse_iso(r.get("reminderTime"))
            if when is None:
                continue
            title = r.get("title") or "Напоминание"
            subtitle = r.get("subtitle")
            summary = f"{title} — {subtitle}" if subtitle else title
            out.append(
                ScheduleEvent(
                    uid=f"reminder-{r.get('id') or ''}",
                    summary=summary,
                    start=when,
                    end=when + timedelta(minutes=1),
                    category="reminder",
                )
            )
    return out


def _parse_iso(value: Any) -> datetime | None:
    """ISO-8601 c 'Z' → aware UTC datetime."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def next_alarm(alarms: list[dict[str, Any]], now: datetime) -> datetime | None:
    """Ближайшее срабатывание любого включённого будильника (или None)."""
    events = alarm_events(alarms, now)
    starts = [e.start for e in events if e.start >= now]
    return min(starts) if starts else None


def next_timer(timers: list[dict[str, Any]], now: datetime) -> datetime | None:
    """Ближайшее окончание таймера (или None)."""
    ends = [e.end for e in timer_events(timers, now) if e.end >= now]
    return min(ends) if ends else None
