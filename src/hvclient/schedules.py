"""Reviewed heating-circuit comfort programs; controller-local, minute-resolution times."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import pairwise
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from .errors import ValidationError

if TYPE_CHECKING:
    from .models import TimeRange, WeeklyTimeRanges


class Weekday(IntEnum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


@dataclass(frozen=True)
class ScheduleLimits:
    """Static reviewed SDK write policy, not device-advertised capabilities.

    These records describe the canonical library validators, not configurable
    overrides. Live circuit/profile/clock eligibility is checked separately.
    Counts and minimums describe replacements, not padded raw controller reads.
    """

    kind: Literal["daily", "weekly"]
    min_day_groups: int | None
    max_day_groups: int | None
    require_all_weekdays: bool
    daily_wire_slots: int | None
    min_windows_per_day: int = 0
    max_windows_per_day: int = 2
    time_resolution_minutes: int = 1
    min_start_time: str = "00:00:00"
    max_start_time: str = "23:59:00"
    min_end_time: str = "00:01:00"
    max_end_time: str = "24:00:00"
    weekdays: tuple[Weekday, ...] = tuple(Weekday)
    allow_overnight: bool = False
    allow_overlaps: bool = False
    require_unique_weekdays: bool = True
    full_replacement: bool = True
    expected_required: bool = True
    unused_slot: tuple[str, str] = ("00:00:00", "00:00:00")
    clock_basis: Literal["controller_local"] = "controller_local"
    scope: Literal["reviewed_sdk_write_policy"] = field(
        default="reviewed_sdk_write_policy", init=False
    )

    @property
    def supports_weekdays(self) -> bool:
        return self.kind == "weekly"

    @property
    def allow_end_of_day(self) -> bool:
        return self.max_end_time == "24:00:00"


DAILY_SCHEDULE_LIMITS = ScheduleLimits(
    kind="daily", min_day_groups=None, max_day_groups=None,
    require_all_weekdays=False, daily_wire_slots=2,
)
WEEKLY_SCHEDULE_LIMITS = ScheduleLimits(
    kind="weekly", min_day_groups=1, max_day_groups=2,
    require_all_weekdays=True, daily_wire_slots=None,
)


@dataclass(frozen=True)
class CircuitProgram:
    name: str
    zone: str
    kind: Literal["daily", "weekly"]
    peer: str
    selector: str

    @property
    def label(self) -> str:
        return f"Heating circuit {self.zone} {self.kind} comfort program"

    @property
    def limits(self) -> ScheduleLimits:
        return DAILY_SCHEDULE_LIMITS if self.kind == "daily" else WEEKLY_SCHEDULE_LIMITS


@dataclass(frozen=True)
class CircuitPrograms:
    zone: str
    daily: CircuitProgram
    weekly: CircuitProgram


CIRCUIT_SCHEDULE_PARAMETERS = MappingProxyType({
    zone: CircuitPrograms(
        zone, CircuitProgram(daily, zone, "daily", weekly, selector),
        CircuitProgram(weekly, zone, "weekly", daily, selector),
    )
    for zone, daily, weekly, selector in (
        ("1", "3", "3a", "A1"), ("2", "6", "6a", "A11"), ("A", "HP3", "HP3a", "A61")
    )
})
SCHEDULE_PARAMETERS = MappingProxyType({
    program.name: program
    for circuit in CIRCUIT_SCHEDULE_PARAMETERS.values()
    for program in (circuit.daily, circuit.weekly)
})


def get_schedule_limits(name: str) -> ScheduleLimits | None:
    """Offline limits for the six reviewed programs; other names return None."""
    program = SCHEDULE_PARAMETERS.get(name)
    return program.limits if program is not None else None


@dataclass(frozen=True)
class DailySchedule:
    time_ranges: tuple[TimeRange, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.time_ranges, (tuple, list)):
            raise ValidationError("daily schedule requires a list/tuple of time ranges")
        object.__setattr__(self, "time_ranges", tuple(self.time_ranges))

    @property
    def limits(self) -> ScheduleLimits:
        return DAILY_SCHEDULE_LIMITS

    def to_json(self) -> list[dict[str, str]]:
        normalized = _ranges(self.time_ranges, self.limits)
        result = [{"start": r.start, "end": r.end} for r in normalized]
        # The reviewed daily setter truncates to its existing two-slot count.
        slots = self.limits.daily_wire_slots
        assert slots is not None
        start, end = self.limits.unused_slot
        return result + [{"start": start, "end": end}] * (slots - len(result))


@dataclass(frozen=True)
class WeeklySchedule:
    groups: tuple[WeeklyTimeRanges, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.groups, (tuple, list)):
            raise ValidationError("weekly schedule requires a list/tuple of day groups")
        object.__setattr__(self, "groups", tuple(self.groups))

    @property
    def limits(self) -> ScheduleLimits:
        return WEEKLY_SCHEDULE_LIMITS

    def to_json(self) -> list[dict[str, Any]]:
        normalized = _weekly(self.groups, full_week=True)
        # Do not send zero-length padding: the weekly JSON parser rejects it.
        return [{"weekdays": list(group.weekdays), "time_ranges": [
            {"start": r.start, "end": r.end} for r in group.time_ranges
        ]} for group in normalized.groups]


ScheduleValue = DailySchedule | WeeklySchedule
_TIME = re.compile(r"(\d{2}):(\d{2})(?::(\d{2}))?", re.ASCII)


def _minutes(value: str) -> int:
    hour, minute, _ = map(int, value.split(":"))
    return hour * 60 + minute


def _time(value: Any, *, end: bool, limits: ScheduleLimits) -> tuple[str, int]:
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValidationError("schedule time must be HH:MM or HH:MM:00")
    hour, minute, second = map(int, (match[1], match[2], match[3] or "0"))
    total = hour * 60 + minute
    low, high = ((limits.min_end_time, limits.max_end_time) if end else
                 (limits.min_start_time, limits.max_start_time))
    if (
        second != 0 or minute > 59 or not _minutes(low) <= total <= _minutes(high)
        or total % limits.time_resolution_minutes != 0
    ):
        raise ValidationError(
            f"schedule {'end' if end else 'start'} must be {low}..{high}, "
            f"on a {limits.time_resolution_minutes}-minute grid"
        )
    return f"{hour:02}:{minute:02}:00", total


def _ranges(value: Any, limits: ScheduleLimits) -> tuple[TimeRange, ...]:
    from .models import TimeRange

    if not isinstance(value, (tuple, list)) or len(value) > limits.max_windows_per_day:
        raise ValidationError(
            f"a reviewed heating program supports at most {limits.max_windows_per_day} time windows"
        )
    result = []
    for item in value:
        if isinstance(item, TimeRange):
            start, end = item.start, item.end
        elif isinstance(item, dict) and set(item) == {"start", "end"}:
            start, end = item["start"], item["end"]
        else:
            raise ValidationError("time ranges require exactly start and end")
        # Explicit unused slots from controller reads are not heating windows.
        if start in (limits.unused_slot[0], limits.unused_slot[0][:5]) and end in (
            limits.unused_slot[1], limits.unused_slot[1][:5]
        ):
            continue
        start, first = _time(start, end=False, limits=limits)
        end, last = _time(end, end=True, limits=limits)
        if first == last or (first > last and not limits.allow_overnight):
            raise ValidationError("schedule windows must have start < end; split overnight periods")
        result.append(TimeRange(start, end))
    result.sort(key=lambda r: r.start)
    if len(result) < limits.min_windows_per_day:
        raise ValidationError(f"schedule needs at least {limits.min_windows_per_day} time windows")
    if not limits.allow_overlaps and any(left.end > right.start for left, right in pairwise(result)):
        raise ValidationError("schedule windows must not overlap")
    return tuple(result)


def _weekday(value: Any, limits: ScheduleLimits) -> int:
    if type(value) not in (int, Weekday) or value not in limits.weekdays:
        raise ValidationError("weekdays must be integer IDs 1=Monday through 7=Sunday or Weekday")
    return int(value)


def _weekly(value: Any, *, full_week: bool) -> WeeklySchedule:
    from .models import WeeklyTimeRanges

    limits = WEEKLY_SCHEDULE_LIMITS
    assert limits.min_day_groups is not None and limits.max_day_groups is not None
    groups = value.groups if isinstance(value, WeeklySchedule) else value
    if (
        not isinstance(groups, (tuple, list)) or len(groups) > limits.max_day_groups
        or (full_week and len(groups) < limits.min_day_groups)
    ):
        raise ValidationError(
            f"weekly programs support {limits.min_day_groups}..{limits.max_day_groups} weekday groups"
        )
    result = []
    seen: set[int] = set()
    for group in groups:
        if isinstance(group, WeeklyTimeRanges):
            days, ranges = group.weekdays, group.time_ranges
        elif isinstance(group, dict) and set(group) == {"weekdays", "time_ranges"}:
            days, ranges = group["weekdays"], group["time_ranges"]
        else:
            raise ValidationError("weekly groups require exactly weekdays and time_ranges")
        if not isinstance(days, (tuple, list)) or len(days) > len(limits.weekdays):
            raise ValidationError("weekdays must be a list/tuple of day IDs")
        normalized_days = tuple(sorted(_weekday(day, limits) for day in days))
        if limits.require_unique_weekdays and (
            len(set(normalized_days)) != len(normalized_days) or seen.intersection(normalized_days)
        ):
            raise ValidationError("a weekday may appear only once across weekly groups")
        windows = _ranges(ranges, limits)
        if not normalized_days:
            if windows:
                raise ValidationError("an unused weekday group must not contain heating windows")
            continue
        seen.update(normalized_days)
        result.append(WeeklyTimeRanges(normalized_days, windows))
    if full_week and limits.require_all_weekdays and seen != set(limits.weekdays):
        raise ValidationError("a weekly replacement must explicitly cover all seven days")
    result.sort(key=lambda group: group.weekdays)
    return WeeklySchedule(tuple(result))


def normalize_schedule(name: str, value: Any, *, replacement: bool = True) -> ScheduleValue:
    program = SCHEDULE_PARAMETERS[name]
    if program.kind == "weekly":
        return _weekly(value, full_week=replacement)
    ranges = value.time_ranges if isinstance(value, DailySchedule) else value
    return DailySchedule(_ranges(ranges, program.limits))


def _merged(ranges: tuple[TimeRange, ...]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in ranges:
        if result and result[-1][1] == item.start:
            result[-1] = (result[-1][0], item.end)
        else:
            result.append((item.start, item.end))
    return tuple(result)


def schedules_equal(name: str, left: Any, right: Any) -> bool:
    """Compare clock behavior, ignoring padding, group order, and equivalent grouping."""
    first = normalize_schedule(name, left, replacement=False)
    second = normalize_schedule(name, right, replacement=False)
    if isinstance(first, DailySchedule) and isinstance(second, DailySchedule):
        return _merged(first.time_ranges) == _merged(second.time_ranges)
    if not isinstance(first, WeeklySchedule) or not isinstance(second, WeeklySchedule):
        return False

    def expand(schedule: WeeklySchedule) -> tuple[Any, ...]:
        per_day = {day: _merged(group.time_ranges) for group in schedule.groups for day in group.weekdays}
        return tuple(per_day.get(day, ()) for day in WEEKLY_SCHEDULE_LIMITS.weekdays)

    return expand(first) == expand(second)
