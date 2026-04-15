from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class CronExpression:
    expression: str
    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    weekdays: set[int]

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        parts = expression.strip().split()
        if len(parts) != 5:
            raise ValueError("Cron expression must have 5 fields: minute hour day month weekday")
        minute, hour, day, month, weekday = parts
        return cls(
            expression=expression.strip(),
            minutes=_parse_field(minute, 0, 59),
            hours=_parse_field(hour, 0, 23),
            days=_parse_field(day, 1, 31),
            months=_parse_field(month, 1, 12),
            weekdays=_parse_field(weekday, 0, 7, normalize_weekday=True),
        )

    def matches(self, value: datetime) -> bool:
        cron_weekday = value.isoweekday() % 7
        return (
            value.minute in self.minutes
            and value.hour in self.hours
            and value.day in self.days
            and value.month in self.months
            and cron_weekday in self.weekdays
        )

    def next_run_after(self, current: datetime) -> datetime:
        candidate = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError(f"Could not find next run time for cron expression: {self.expression}")


def _parse_field(raw: str, minimum: int, maximum: int, *, normalize_weekday: bool = False) -> set[int]:
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Invalid empty cron field segment in '{raw}'")
        if part == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError(f"Invalid cron step '{part}'")
            values.update(range(minimum, maximum + 1, step))
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"Invalid cron range '{part}'")
            for value in range(start, end + 1):
                values.add(_normalize_value(value, minimum, maximum, normalize_weekday))
            continue
        value = int(part)
        values.add(_normalize_value(value, minimum, maximum, normalize_weekday))
    return values


def _normalize_value(value: int, minimum: int, maximum: int, normalize_weekday: bool) -> int:
    if normalize_weekday and value == 7:
        value = 0
    if value < minimum or value > maximum:
        raise ValueError(f"Cron value {value} outside allowed range {minimum}-{maximum}")
    return value
