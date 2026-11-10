"""Deny-by-default writes, never inferred from the firmware's catalog levels."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from .curves import HEATING_CURVES
from .errors import ValidationError, WritePolicyError
from .models import Choice, Parameter, TimeRange, WeeklyTimeRanges
from .modes import CIRCUIT_MODES, MODE_IDS, mode_id
from .schedules import (
    SCHEDULE_PARAMETERS,
    ScheduleValue,
    normalize_schedule,
    schedules_equal,
)

# Only ordinary numeric temperature settings, not mode/ignition/reset commands.
# This is a semantic restriction independent of the caller's opt-in and USER.
NUMERIC_USER_WRITES = MappingProxyType({
    "4": "FLOAT", "5": "FLOAT", "7": "FLOAT", "8": "FLOAT",
    "HP4": "FLOAT", "HP5": "FLOAT", "2": "INTEGER", "HP2": "INTEGER",
    "11": "INTEGER", "12": "INTEGER", "13": "INTEGER",
})
HEATING_CURVE_WRITES = MappingProxyType({name: "FLOAT" for name in HEATING_CURVES})
NUMERIC_WRITES = MappingProxyType({**NUMERIC_USER_WRITES, **HEATING_CURVE_WRITES})
CIRCUIT_MODE_WRITES = MappingProxyType({name: "CHOICE" for name in CIRCUIT_MODES})
CIRCUIT_SCHEDULE_WRITES = MappingProxyType({
    name: "DAILY_CLOCK_RANGE" if program.kind == "daily" else "WEEKLY_CLOCK_RANGE"
    for name, program in SCHEDULE_PARAMETERS.items()
})
PARAMETER_WRITES = MappingProxyType({**NUMERIC_WRITES, **CIRCUIT_MODE_WRITES, **CIRCUIT_SCHEDULE_WRITES})


@dataclass(frozen=True)
class WritePolicy:
    allowed_names: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if isinstance(self.allowed_names, str):
            raise WritePolicyError("allowed_names must be a collection of API names")
        try:
            names = frozenset(self.allowed_names)
        except TypeError as exc:
            raise WritePolicyError("invalid allowed_names") from exc
        if any(not isinstance(name, str) or name not in PARAMETER_WRITES for name in names):
            raise WritePolicyError("write policy contains an unreviewed parameter name")
        object.__setattr__(self, "allowed_names", names)

    @classmethod
    def only(cls, *names: str) -> WritePolicy:
        return cls(frozenset(names))

    def require(self, name: str) -> None:
        if name not in self.allowed_names or name not in PARAMETER_WRITES:
            raise WritePolicyError(f"writes to {name!r} are disabled")


def decimal_number(value: Any) -> Decimal:
    if type(value) not in (int, float, Decimal):
        raise ValidationError("expected a number, not a boolean/string/structured value")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValidationError("numbers must be finite")
    parts = number.as_tuple()
    if (
        not isinstance(parts.exponent, int)
        or len(parts.digits) > 1000 or not -1000 <= parts.exponent <= 1000
    ):
        raise ValidationError("numeric precision/exponent exceeds the supported range")
    return number


def numeric_input(name: str, value: Any) -> Decimal:
    number = decimal_number(value)
    if NUMERIC_WRITES[name] == "INTEGER" and type(value) is not int:
        raise ValidationError("INTEGER parameters require an integer")
    return number


def parameter_input(name: str, value: Any) -> Any:
    if name in SCHEDULE_PARAMETERS:
        return normalize_schedule(name, value)
    if name in CIRCUIT_MODES:
        return mode_id(value)
    numeric_input(name, value)
    return value


def stored_value(parameter: Parameter) -> int | float | ScheduleValue:
    if parameter.name in SCHEDULE_PARAMETERS:
        return normalize_schedule(parameter.name, parameter.value, replacement=False)
    if parameter.name in CIRCUIT_MODES:
        if not isinstance(parameter.value, Choice):
            raise ValidationError("mode parameter does not contain a choice")
        return mode_id(parameter.value.id)
    number = parameter.value
    decimal_number(number)
    if isinstance(number, (int, float)):
        return number
    raise ValidationError("numeric parameter does not contain a JSON number")


def wire_value(name: str, requested: Any) -> Any:
    if name in SCHEDULE_PARAMETERS:
        return normalize_schedule(name, requested).to_json()
    return {"id": mode_id(requested)} if name in CIRCUIT_MODES else requested


def values_equal(name: str, left: Any, right: Any) -> bool:
    if name in SCHEDULE_PARAMETERS:
        return schedules_equal(name, left, right)
    return decimal_number(left) == decimal_number(right)


def validate_mode_parameter(parameter: Parameter) -> None:
    if (
        parameter.name not in CIRCUIT_MODES or parameter.security_level != "DEVELOPER"
        or parameter.type != "CHOICE" or parameter.active is not False
        or parameter.unit not in (None, "")
    ):
        raise WritePolicyError("circuit-mode hidden-choice metadata differs from the reviewed policy")
    # AlwaysInactiveCondition deliberately hides these exact parameters. This
    # exception must never become a generic inactive/developer-write override.
    ids = [choice.id for choice in parameter.choices]
    if len(ids) != len(MODE_IDS) or set(ids) != MODE_IDS:
        raise ValidationError("circuit-mode choices must contain exactly IDs 0..5")
    stored_value(parameter)


def validate_change(parameter: Parameter, value: Any, policy: WritePolicy) -> int | float | ScheduleValue:
    policy.require(parameter.name)
    if parameter.name in SCHEDULE_PARAMETERS:
        validate_schedule_parameter(parameter, active=True)
        return normalize_schedule(parameter.name, value)
    if parameter.name in CIRCUIT_MODES:
        validate_mode_parameter(parameter)
        return mode_id(value)
    is_curve = parameter.name in HEATING_CURVES
    expected_level = "PLUMBER" if is_curve else "USER"
    expected_unit = "" if is_curve else "°C"
    if (
        parameter.security_level != expected_level
        or parameter.type != NUMERIC_WRITES[parameter.name]
    ):
        raise WritePolicyError("parameter classification/type differs from the reviewed policy")
    if parameter.active is not True:
        raise ValidationError("parameter is inactive or its active state is unknown")
    if parameter.unit != expected_unit:
        raise ValidationError("parameter unit differs from the reviewed setting")
    requested = numeric_input(parameter.name, value)
    if any(v is None for v in (parameter.minimum, parameter.maximum, parameter.increment)):
        raise ValidationError("live minimum, maximum, and increment are required")
    low, high, step = map(decimal_number, (
        parameter.minimum, parameter.maximum, parameter.increment
    ))
    if low > high or step <= 0:
        raise ValidationError("invalid live limits/increment")
    if not low <= requested <= high:
        raise ValidationError(f"requested value is outside the live range {low} to {high}")
    # Exact rational arithmetic also avoids dependence on Decimal's global precision.
    grid_position = (Fraction(requested) - Fraction(low)) / Fraction(step)
    if grid_position.denominator != 1:
        raise ValidationError(f"requested value is not aligned to the live increment {step}")
    try:
        wire = int(requested) if parameter.type == "INTEGER" else float(requested)
        if math.isfinite(wire) and Decimal(str(wire)) == requested:
            return wire
    except (ValueError, OverflowError):
        pass
    raise ValidationError("requested value cannot be faithfully represented as a JSON number")


def validate_circuit_type(selector: Any, name: str) -> int:
    if (
        not isinstance(selector, dict) or selector.get("name") != name
        or selector.get("securityLevel") != "PLUMBER" or selector.get("type") != "CHOICE"
        or selector.get("active") is not True or not isinstance(selector.get("value"), dict)
    ):
        raise ValidationError("circuit-type context is missing or changed")
    choice = selector["value"].get("id")
    if type(choice) is not int or choice not in (1, 2):
        raise ValidationError("circuit is absent or has an unsupported configured type")
    return choice


def validate_curve_context(parameter: Parameter, peer: Parameter, selector: Any) -> None:
    """Reject inactive/ambiguous profiles; never write a selector or its peer."""
    profile = HEATING_CURVES[parameter.name]
    if validate_circuit_type(selector, profile.selector) != profile.choice_id:
        raise ValidationError("heating-curve name does not match the enabled circuit type")
    if (
        parameter.active is not True or peer.active is not False
        or peer.name != profile.peer
        or any(p.security_level != "PLUMBER" or p.type != "FLOAT" or p.unit != ""
               for p in (parameter, peer))
    ):
        raise ValidationError("heating-curve profile metadata is inactive, ambiguous, or changed")


def validate_mode_context(
    parameter: Parameter, selector: Any, expected_type: int | None = None
) -> int:
    validate_mode_parameter(parameter)
    configured = validate_circuit_type(selector, CIRCUIT_MODES[parameter.name])
    if expected_type is not None and configured != expected_type:
        raise ValidationError("circuit type changed during mode write/read-back")
    return configured


def validate_schedule_parameter(parameter: Parameter, *, active: bool) -> None:
    if (
        parameter.name not in SCHEDULE_PARAMETERS or parameter.security_level != "USER"
        or parameter.type != CIRCUIT_SCHEDULE_WRITES[parameter.name]
        or parameter.active is not active or parameter.unit not in (None, "")
    ):
        raise WritePolicyError("circuit-schedule metadata differs from the reviewed policy")
    if active:
        value = parameter.value
        limits = SCHEDULE_PARAMETERS[parameter.name].limits
        if limits.kind == "daily":
            if not isinstance(value, tuple) or len(value) != limits.daily_wire_slots or not all(isinstance(v, TimeRange) for v in value):
                raise ValidationError(f"reviewed daily programs require {limits.daily_wire_slots} controller slots")
        else:
            assert limits.min_day_groups is not None and limits.max_day_groups is not None
            if not isinstance(value, tuple) or not limits.min_day_groups <= len(value) <= limits.max_day_groups or not all(isinstance(v, WeeklyTimeRanges) for v in value):
                raise ValidationError(
                    f"reviewed weekly programs require {limits.min_day_groups}..{limits.max_day_groups} day groups"
                )
        stored_value(parameter)


def validate_schedule_context(
    parameter: Parameter, peer: Parameter, selector: Any, clock: Any,
    expected_type: int | None = None,
) -> int:
    program = SCHEDULE_PARAMETERS[parameter.name]
    validate_schedule_parameter(parameter, active=True)
    if peer.name != program.peer:
        raise ValidationError("circuit-schedule peer has changed")
    validate_schedule_parameter(peer, active=False)
    if (
        not isinstance(clock, dict) or clock.get("name") != "D9"
        or clock.get("securityLevel") != "PLUMBER" or clock.get("type") != "BOOLEAN"
        or clock.get("active") is not True or type(clock.get("value")) is not bool
        or clock["value"] is not (program.kind == "weekly")
    ):
        raise ValidationError("program is not selected by the global daily/weekly setting D9")
    configured = validate_circuit_type(selector, program.selector)
    if expected_type is not None and configured != expected_type:
        raise ValidationError("circuit type changed during schedule write/read-back")
    return configured
