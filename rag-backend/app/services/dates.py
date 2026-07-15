"""German-formatted current date, e.g. "Mittwoch, 15. Juli 2026".

Formatted manually so it never depends on a de_DE locale being installed in
the container. Used to ground the LLM (system prompt) and the web query
rewriter ("gestern" → an absolute date) in the actual current date.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_WEEKDAYS = [
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag",
]
_MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def today_german(tz: str = "Europe/Berlin") -> str:
    now = datetime.now(ZoneInfo(tz))
    return f"{_WEEKDAYS[now.weekday()]}, {now.day}. {_MONTHS[now.month - 1]} {now.year}"
