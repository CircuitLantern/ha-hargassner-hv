"""Per-circuit mode IDs, never boiler-wide operating modes or SERVICE requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType
from typing import Any, Literal

from .errors import ValidationError
from .schedules import SCHEDULE_PARAMETERS


class HeatingCircuitMode(IntEnum):
    OFF = 0
    AUTOMATIC = 1
    PERMANENT_REDUCED = 2
    PERMANENT_COMFORT = 3
    ONCE_COMFORT = 4
    ONCE_REDUCED = 5


@dataclass(frozen=True)
class ParameterMeaning:
    """Reviewed English semantics, independent of localized firmware metadata.

    This describes a parameter; it grants no access or write permission and
    does not establish circuit presence or the installation's physical room map.
    """

    name: str
    zone: str
    purpose: Literal["operating_mode", "comfort_schedule"]
    label: str
    description: str


_MODE_DEFINITIONS = (("1", "A1z", "A1"), ("2", "A11z", "A11"), ("A", "A61z", "A61"))

# Public zone-keyed registry lets callers resolve the wire name without
# hard-coding it. The records and both registry layers are immutable.
CIRCUIT_MODE_PARAMETERS = MappingProxyType({
    zone: ParameterMeaning(
        name=name, zone=zone, purpose="operating_mode",
        label=f"Heating circuit {zone} operating mode",
        description=(
            f"Selects the operating mode of space-heating circuit {zone}: off, automatic, "
            "permanent reduced, permanent comfort, one-time comfort, or one-time reduced. "
            "This is not the boiler-wide operating mode."
        ),
    )
    for zone, name, _ in _MODE_DEFINITIONS
})
_MEANINGS_BY_NAME = MappingProxyType({
    **{p.name: p for p in CIRCUIT_MODE_PARAMETERS.values()},
    **{name: ParameterMeaning(
        name, program.zone, "comfort_schedule", program.label,
        f"Defines {program.kind} comfort-heating windows for circuit {program.zone}; "
        "outside those windows Automatic normally uses the reduced room target. "
        "This is not a DHW program or an operating-mode switch.",
    ) for name, program in SCHEDULE_PARAMETERS.items()},
})

# Internal read-only circuit-type validation context, derived from the same
# definitions. These are not aliases for SystemModel modes.
CIRCUIT_MODES = MappingProxyType({name: selector for _, name, selector in _MODE_DEFINITIONS})
MODE_IDS = frozenset(int(mode) for mode in HeatingCircuitMode)


def get_parameter_meaning(name: str) -> ParameterMeaning | None:
    """Look up reviewed English semantics offline; unknown/unmapped names return None."""
    return _MEANINGS_BY_NAME.get(name)


def mode_id(value: Any) -> int:
    # In particular reject bool, float, Decimal, labels, wire dictionaries, and
    # unrelated IntEnums (e.g. boiler-wide mode enums with overlapping values).
    if type(value) not in (int, HeatingCircuitMode) or value not in MODE_IDS:
        raise ValidationError("circuit mode must be an integer ID 0..5 or HeatingCircuitMode")
    return int(value)
