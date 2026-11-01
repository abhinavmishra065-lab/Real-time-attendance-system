"""School-local clock helpers for scheduled attendance."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app

DEFAULT_TIMEZONE = "Asia/Kolkata"


def school_timezone() -> ZoneInfo:
    """Return the configured timezone used by the school timetable."""
    return ZoneInfo(current_app.config.get("TIMEZONE", DEFAULT_TIMEZONE))


def local_now() -> datetime:
    """Return the current school-local time as a naive datetime for the database."""
    return datetime.now(school_timezone()).replace(tzinfo=None)


def local_today() -> date:
    """Return the current date at the school."""
    return local_now().date()


def local_date_from_utc(value: datetime) -> date:
    """Interpret a stored naive UTC timestamp as a date in the school timezone."""
    return value.replace(tzinfo=timezone.utc).astimezone(school_timezone()).date()


def utc_naive_from_local(value: datetime) -> datetime:
    """Convert a school-local datetime to the naive UTC format used by the database."""
    return value.replace(tzinfo=school_timezone()).astimezone(timezone.utc).replace(
        tzinfo=None
    )
