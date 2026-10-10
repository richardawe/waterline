"""West Africa Time helpers: contact window, working days, holidays."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")
CONTACT_START = time(8, 0)  # baseline; each segment's policy may be stricter
CONTACT_END = time(19, 0)


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def window(policy: dict | None) -> tuple[time, time]:
    if not policy:
        return CONTACT_START, CONTACT_END
    return _hhmm(policy["contact_start"]), _hhmm(policy["contact_end"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_lagos(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LAGOS)


def lagos_date(dt: datetime) -> date:
    return to_lagos(dt).date()


def lagos_datetime(d: date, t: time) -> datetime:
    """A WAT wall-clock moment as an aware UTC datetime."""
    return datetime.combine(d, t, tzinfo=LAGOS).astimezone(timezone.utc)


def is_contact_day(d: date, holidays: set[date], policy: dict | None = None) -> bool:
    """Policy weekdays (baseline Monday–Saturday), never Sunday, never a Nigerian public holiday."""
    weekdays = policy["contact_weekdays"] if policy else range(6)
    return d.weekday() != 6 and d.weekday() in weekdays and d not in holidays


def in_contact_window(now: datetime, holidays: set[date], policy: dict | None = None) -> bool:
    local = to_lagos(now)
    start, end = window(policy)
    return is_contact_day(local.date(), holidays, policy) and start <= local.time() < end


def is_working_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def working_days_between(start: date, end: date, holidays: set[date]) -> int:
    """Working days strictly after `start`, up to and including `end`."""
    days, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_working_day(d, holidays):
            days += 1
    return days
