"""Time, date, size and speed formatting in Brazilian Portuguese.

Everything here is locale-independent on purpose: the pt_BR locale is not
always installed, and the app's interface is written in Portuguese.
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime

MONTHS_SHORT = ("jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
                "jul.", "ago.", "set.", "out.", "nov.", "dez.")
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo")


def format_clock(seconds: float | None) -> str:
    """0:42, 12:05 or 1:02:03."""
    if seconds is None or seconds != seconds or seconds < 0:  # None, NaN, negative
        seconds = 0
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_remaining(position: float, duration: float) -> str:
    """Remaining time with a leading minus sign, e.g. -23:41."""
    if not duration or duration <= 0:
        return "--:--"
    remaining = max(0.0, duration - max(position, 0.0))
    return "-" + format_clock(math.ceil(remaining))


def format_duration(seconds: float | None) -> str:
    """Human duration: 1h 22min, 45min, 30s."""
    if not seconds or seconds <= 0:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours}h {minutes}min"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}min"
    return f"{total}s"


def format_time_left(position: float, duration: float) -> str:
    """Faltam 23 min / Faltam 1h 05min / Falta menos de 1 min."""
    if not duration or duration <= 0:
        return ""
    remaining = max(0, int(duration - position))
    if remaining < 60:
        return "Falta menos de 1 min"
    hours, rest = divmod(remaining, 3600)
    minutes = rest // 60
    if hours:
        return f"Faltam {hours}h {minutes:02d}min"
    return f"Faltam {minutes} min"


def _to_date(timestamp: float) -> date:
    return datetime.fromtimestamp(timestamp).date()


def format_date(timestamp: float | None, now: float | None = None) -> str:
    """Hoje, Ontem, Quarta-feira, 25 de set. or 25 de set. de 2024."""
    if not timestamp or timestamp <= 0:
        return ""
    today = _to_date(now if now is not None else time.time())
    day = _to_date(timestamp)
    delta = (today - day).days
    if delta == 0:
        return "Hoje"
    if delta == 1:
        return "Ontem"
    if 1 < delta < 7:
        return WEEKDAYS[day.weekday()].capitalize()
    label = f"{day.day} de {MONTHS_SHORT[day.month - 1]}"
    if day.year != today.year:
        label += f" de {day.year}"
    return label


def format_long_date(timestamp: float | None) -> str:
    """25 de set. de 2026 (always with the year)."""
    if not timestamp or timestamp <= 0:
        return ""
    day = _to_date(timestamp)
    return f"{day.day} de {MONTHS_SHORT[day.month - 1]} de {day.year}"


def parse_duration(text: str | None) -> int:
    """Parse itunes:duration values: '3723', '62:03', '1:02:03', '3723.5'."""
    if not text:
        return 0
    text = text.strip()
    try:
        if ":" not in text:
            return max(0, int(float(text)))
        total = 0
        for part in text.split(":"):
            total = total * 60 + int(float(part or 0))
        return max(0, total)
    except ValueError:
        return 0


def format_speed(rate: float) -> str:
    """1×, 1,25×, 0,75×."""
    text = f"{rate:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") + "×"


def format_size(num_bytes: int | None) -> str:
    """45,2 MB."""
    if not num_bytes or num_bytes <= 0:
        return ""
    units = ("B", "KB", "MB", "GB")
    value = float(num_bytes)
    unit = 0
    while value >= 1000 and unit < len(units) - 1:
        value /= 1000
        unit += 1
    if unit == 0:
        return f"{int(value)} B"
    return f"{value:.1f}".replace(".", ",") + f" {units[unit]}"
