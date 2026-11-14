# SPDX-License-Identifier: Apache-2.0

"""Immutable parameter, telemetry, and information values; no unit/timezone guesses."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from .catalog import supported_parameter
from .errors import ParameterAccessError, ProtocolError, ValidationError
from .modes import CIRCUIT_MODES, ParameterMeaning, get_parameter_meaning
from .schedules import (
    SCHEDULE_PARAMETERS,
    DailySchedule,
    ScheduleLimits,
    WeeklySchedule,
    get_schedule_limits,
    schedules_equal,
)


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
    # Numeric IDs are preserved. The writer exposes Qt's ISO map through Weekday.
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
        raise ProtocolError("expected a JSON number, not a boolean or structured/text value")
    try:
        if math.isfinite(value):
            return value
    except OverflowError:
        pass
    raise ProtocolError("numeric JSON values must be finite")


def _snapshot_object(document: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(document, dict):
        raise ProtocolError(f"{label} response is not a JSON object")
    try:
        return freeze_json(document)
    except RecursionError as exc:
        raise ProtocolError(f"{label} JSON nesting exceeds the supported depth") from exc


@dataclass(frozen=True, eq=False)
class ChannelSnapshot(Mapping[str, int | float]):
    """Dynamic channel-name -> number mapping; no inferred units or enum meanings."""

    channels: Mapping[str, int | float] = field(repr=False)

    def __getitem__(self, name: str) -> int | float:
        return self.channels[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.channels)

    def __len__(self) -> int:
        return len(self.channels)

    def to_dict(self) -> dict[str, int | float]:
        return dict(self.channels)


def channels_from_json(document: Any) -> ChannelSnapshot:
    if not isinstance(document, dict):
        raise ProtocolError("channels response is not a JSON object")
    channels = {}
    for name, value in document.items():
        if not isinstance(name, str) or not name:
            raise ProtocolError("channel name must be nonempty text")
        channels[name] = _number(value)
    return ChannelSnapshot(MappingProxyType(channels))


@dataclass(frozen=True)
class SystemInfo:
    """Identity strings may be absent/null on other firmware; never invent IDs.

    Known identifiers and raw extensions are excluded from repr. to_dict()
    deliberately returns them for callers that explicitly request identity data.
    """

    raw: Mapping[str, Any] = field(repr=False)
    software_version: str | None = None
    mac: str | None = field(default=None, repr=False)
    system_id: str | None = field(default=None, repr=False)
    commission_number: str | None = field(default=None, repr=False)
    article_serial_number: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Detached wire-shaped copy; contains private device identifiers."""
        return thaw_json(self.raw)


def system_info_from_json(document: Any) -> SystemInfo:
    raw = _snapshot_object(document, "info")

    def text(key: str) -> str | None:
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ProtocolError(f"info field {key} must be text or null")
        return value

    return SystemInfo(
        raw=raw, software_version=text("softwareVersion"), mac=text("mac"),
        system_id=text("systemId"), commission_number=text("commissionNumber"),
        article_serial_number=text("articleSerialNumber"),
    )


@dataclass(frozen=True)
class PcbInfo:
    internal_temperature: int | float
    internal_voltage: int | float
    raw: Mapping[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return thaw_json(self.raw)


def pcb_info_from_json(document: Any) -> PcbInfo:
    raw = _snapshot_object(document, "pcb")
    if "internalTemperature" not in raw or "internalVoltage" not in raw:
        raise ProtocolError("pcb response is missing internalTemperature/internalVoltage")
    return PcbInfo(
        internal_temperature=_number(raw["internalTemperature"]),
        internal_voltage=_number(raw["internalVoltage"]), raw=raw,
    )


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
    security_level: Literal["USER", "PLUMBER", "DEVELOPER"] = "USER"

    @property
    def meaning(self) -> ParameterMeaning | None:
        """Reviewed library semantics, separate from controller descriptions/raw data."""
        return get_parameter_meaning(self.name)

    @property
    def schedule_limits(self) -> ScheduleLimits | None:
        """Static SDK write envelope for reviewed programs, not live eligibility."""
        return get_schedule_limits(self.name)

    def to_dict(self) -> dict[str, Any]:
        """Detached wire-shaped data, without library meanings or catalog children."""
        return thaw_json(self.raw)


def parameter_from_json(row: Any) -> Parameter:
    if not isinstance(row, dict):
        raise ProtocolError("parameter is not an object")
    if not supported_parameter(row.get("name"), row.get("securityLevel")):
        raise ParameterAccessError("parameter is outside USER and the reviewed curve/mode exceptions")
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
        security_level=("USER" if row["securityLevel"] == "USER" else
                        "PLUMBER" if row["securityLevel"] == "PLUMBER" else "DEVELOPER"),
    )


@dataclass(frozen=True)
class WritePreview:
    parameter: Parameter
    requested: int | float | DailySchedule | WeeklySchedule
    circuit_type_id: int | None = None


@dataclass(frozen=True)
class WriteResult:
    name: str
    requested: int | float | DailySchedule | WeeklySchedule
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
        if self.name in SCHEDULE_PARAMETERS:
            try:
                return schedules_equal(self.name, self.after.value, self.requested)
            except ValidationError:
                return False
        if self.name in CIRCUIT_MODES and isinstance(self.after.value, Choice):
            return type(self.requested) is int and self.after.value.id == self.requested
        return type(self.after.value) in (int, float) and self.after.value == self.requested
