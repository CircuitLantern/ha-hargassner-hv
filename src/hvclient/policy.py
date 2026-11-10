"""Deny-by-default writes, never inferred from the firmware's catalog levels."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from .errors import ValidationError, WritePolicyError
from .models import Parameter

# Only ordinary numeric temperature settings, not mode/ignition/reset commands.
# This is a semantic restriction independent of the caller's opt-in and USER.
NUMERIC_USER_WRITES = MappingProxyType({
    "4": "FLOAT", "5": "FLOAT", "7": "FLOAT", "8": "FLOAT",
    "HP4": "FLOAT", "HP5": "FLOAT", "2": "INTEGER", "HP2": "INTEGER",
    "11": "INTEGER", "12": "INTEGER", "13": "INTEGER",
})


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
        if any(not isinstance(name, str) or name not in NUMERIC_USER_WRITES for name in names):
            raise WritePolicyError("write policy contains an unreviewed parameter name")
        object.__setattr__(self, "allowed_names", names)

    @classmethod
    def only(cls, *names: str) -> WritePolicy:
        return cls(frozenset(names))

    def require(self, name: str) -> None:
        if name not in self.allowed_names or name not in NUMERIC_USER_WRITES:
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
    if NUMERIC_USER_WRITES[name] == "INTEGER" and type(value) is not int:
        raise ValidationError("INTEGER parameters require an integer")
    return number


def validate_change(parameter: Parameter, value: Any, policy: WritePolicy) -> int | float:
    policy.require(parameter.name)
    if parameter.security_level != "USER" or parameter.type != NUMERIC_USER_WRITES[parameter.name]:
        raise WritePolicyError("parameter classification/type differs from the reviewed policy")
    if parameter.active is not True:
        raise ValidationError("parameter is inactive or its active state is unknown")
    if parameter.unit != "°C":
        raise ValidationError("parameter unit differs from the reviewed Celsius setting")
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
