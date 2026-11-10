"""Immutable USER parameter values and metadata; no schedule/timezone guesses."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from .errors import ParameterAccessError, ProtocolError


def freeze_json(value: Any) -> Any:
    """Copy JSON into immutable containers and reject non-finite numbers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    raise ProtocolError("response contains an invalid JSON value")


def thaw_json(value: Any) -> Any:
    """Return a detached, ordinary JSON-compatible copy, e.g. for CLI output."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class Choice:
    id: int
    name: str


@dataclass(frozen=True)
class TimeRange:
    # Keep 24:00:00 and zero-length slots exactly; neither is datetime.time.
    start: str
    end: str


@dataclass(frozen=True)
class WeeklyTimeRanges:
    # Numeric weekdays are preserved, not assigned an unverified weekday map.
    weekdays: tuple[int, ...]
    time_ranges: tuple[TimeRange, ...]


@dataclass(frozen=True)
class ControllerDateTime:
    # Unset/invalid components (including zero/-1) occur in real responses.
    # Do not silently substitute dates or assume local time/UTC.
    year: int
    month: int
    day: int
    hours: int
    minutes: int
    seconds: int


ParameterValue = (
    int | float | bool | str | Choice | ControllerDateTime
    | tuple[Choice, ...] | tuple[TimeRange, ...] | tuple[WeeklyTimeRanges, ...]
    | Mapping[str, Any] | tuple[Any, ...] | None
)


def _choice(value: Any) -> Choice:
    if (
        not isinstance(value, dict)
        or type(value.get("id")) is not int
        or not isinstance(value.get("name"), str)
    ):
        raise ProtocolError("invalid choice value")
    return Choice(value["id"], value["name"])


def _time_ranges(value: Any) -> tuple[TimeRange, ...]:
    if not isinstance(value, list):
        raise ProtocolError("invalid daily time ranges")
    ranges = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("start"), str)
            or not isinstance(item.get("end"), str)
        ):
            raise ProtocolError("invalid time range")
        ranges.append(TimeRange(item["start"], item["end"]))
    return tuple(ranges)


def _number(value: Any) -> int | float:
    if type(value) not in (int, float):
        raise ProtocolError("expected a numeric parameter value/metadata field")
    try:
        if math.isfinite(value):
            return value
    except OverflowError:
        pass
    raise ProtocolError("numeric parameter value/metadata must be finite")


def decode_value(type_name: str, value: Any) -> ParameterValue:
    if type_name == "INTEGER":
        if type(value) is not int:
            raise ProtocolError("INTEGER value is not a JSON integer")
        return _number(value)
    if type_name == "FLOAT":
        return float(_number(value))
    if type_name == "BOOLEAN":
        if type(value) is not bool:
            raise ProtocolError("BOOLEAN value is not a JSON boolean")
        return value
    if type_name == "TEXT":
        if not isinstance(value, str):
            raise ProtocolError("TEXT value is not a JSON string")
        return value
    if type_name == "CHOICE":
        return _choice(value)
    if type_name == "MULTIPLE_CHOICE":
        if not isinstance(value, list):
            raise ProtocolError("invalid multiple-choice value")
        return tuple(_choice(item) for item in value)
    if type_name == "DAILY_CLOCK_RANGE":
        return _time_ranges(value)
    if type_name == "WEEKLY_CLOCK_RANGE":
        if not isinstance(value, list):
            raise ProtocolError("invalid weekly time ranges")
        groups = []
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("weekdays"), list):
                raise ProtocolError("invalid weekly time range group")
            if any(type(day) is not int for day in item["weekdays"]):
                raise ProtocolError("weekdays must be integers")
            groups.append(WeeklyTimeRanges(
                tuple(item["weekdays"]), _time_ranges(item.get("time_ranges"))
            ))
        return tuple(groups)
    if type_name == "DATETIME":
        keys = ("year", "month", "day", "hours", "minutes", "seconds")
        if not isinstance(value, dict) or any(type(value.get(k)) is not int for k in keys):
            raise ProtocolError("invalid controller date/time components")
        return ControllerDateTime(**{key: value[key] for key in keys})
    # Unknown USER types remain inspectable but never become writable implicitly.
    return freeze_json(value)


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str
    value: ParameterValue
    active: bool | None
    display_name: str | None
    description: str | None
    help_text: str | None
    unit: str | None
    minimum: int | float | None
    maximum: int | float | None
    increment: int | float | None
    choices: tuple[Choice, ...]
    true_text: str | None
    false_text: str | None
    raw: Mapping[str, Any] = field(repr=False)
    security_level: Literal["USER"] = field(default="USER", init=False)

    def to_dict(self) -> dict[str, Any]:
        """Detached wire-shaped USER data; nested catalog children are excluded."""
        return thaw_json(self.raw)


def parameter_from_json(row: Any) -> Parameter:
    if not isinstance(row, dict):
        raise ProtocolError("parameter is not an object")
    if row.get("securityLevel") != "USER":
        raise ParameterAccessError("only USER parameters are supported")
    if not isinstance(row.get("name"), str) or not row["name"]:
        raise ProtocolError("parameter has no API name")
    if not isinstance(row.get("type"), str) or "value" not in row:
        raise ProtocolError("parameter is missing its type/value")
    active = row.get("active")
    if active is not None and type(active) is not bool:
        raise ProtocolError("parameter active flag is not boolean")

    def text(key: str) -> str | None:
        value = row.get(key)
        if value is not None and not isinstance(value, str):
            raise ProtocolError(f"parameter {key} is not text")
        return value

    def number(key: str) -> int | float | None:
        value = row.get(key)
        return None if value is None else _number(value)

    choices = row.get("choices", [])
    if not isinstance(choices, list):
        raise ProtocolError("parameter choices is not an array")
    # Never expose higher-level parameter objects nested under catalog children.
    raw = freeze_json({key: value for key, value in row.items() if key != "children"})
    return Parameter(
        name=row["name"], type=row["type"], value=decode_value(row["type"], row["value"]),
        active=active, display_name=text("displayName"), description=text("description"),
        help_text=text("helpText"), unit=text("unit"), minimum=number("minimum"),
        maximum=number("maximum"), increment=number("increment"),
        choices=tuple(_choice(item) for item in choices),
        true_text=text("trueText"), false_text=text("falseText"), raw=raw,
    )


@dataclass(frozen=True)
class WritePreview:
    parameter: Parameter
    requested: int | float


@dataclass(frozen=True)
class WriteResult:
    name: str
    requested: int | float
    before: Parameter
    after: Parameter | None
    returned: Parameter | None
    outcome: Literal["verified", "unchanged", "mismatch", "unknown"]
    write_status: int | None = None

    @property
    def verified(self) -> bool:
        return self.outcome in {"verified", "unchanged"}

    @property
    def read_back_matches(self) -> bool | None:
        if self.after is None:
            return None
        return type(self.after.value) in (int, float) and self.after.value == self.requested
