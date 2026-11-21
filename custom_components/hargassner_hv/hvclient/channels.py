# SPDX-License-Identifier: Apache-2.0

"""Opt-in channel interpretation; raw snapshots and write policy stay unchanged.

Boiler/circuit definitions are evidenced in firmware 2.7.4, not a guarantee
across versions. Door-contact polarity is a live observation on 2026-11-20,
with firmware unspecified. None of these readings certifies physical safety.
See docs/channel-guide.md for symbols, manual references, and limitations.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType
from typing import Literal

from .modes import HeatingCircuitMode


class BoilerState(IntEnum):
    """Shared KesselState codes; not all are necessarily used by Smart HV."""

    OFF = 0
    IGNITION_PREPARATION = 1
    IGNITION_MONITORING = 2
    IGNITION_PAUSE = 3
    TRANSITION_TO_POWER_BURNING = 4
    STARTUP = 5
    POWER_BURNING = 6
    EMBER_MAINTENANCE = 7
    BURNOUT = 8
    DE_ASHING = 9
    RESIDUAL_HEAT = 10
    MANUAL = 11
    OVERTEMPERATURE = 12
    EMBER_MAINTENANCE_LOCKOUT = 13


class HeatingCircuitRegulationState(IntEnum):
    """HeizkreisZustand codes, distinct from HeatingCircuitMode (Status)."""

    OFF = 0
    COMFORT = 1
    REDUCED = 2
    FROST_PROTECTION = 3
    DAYTIME_REDUCTION_SHUTDOWN = 4
    NIGHTTIME_REDUCTION_SHUTDOWN = 5
    OUTDOOR_TEMPERATURE_SHUTDOWN = 6
    OVERTEMPERATURE = 7
    CHIMNEY_SWEEP = 8
    RESIDUAL_HEAT = 9


class DoorContactState(IntEnum):
    """Observed polarity only; OPEN may also indicate a disconnected/broken cable."""

    OPEN = 0
    CLOSED = 1


ChannelState = BoilerState | HeatingCircuitMode | HeatingCircuitRegulationState | DoorContactState


@dataclass(frozen=True)
class ChannelMeaning:
    """Reviewed offline interpretation, not firmware negotiation or write access.

    Labels are readable English, not localized device metadata. Decoding is
    explicit and accepts exact JSON integer codes or integer-valued floats.
    Unknown codes/invalid inputs return None; the caller retains the raw value.
    """

    name: str
    label: str
    enum_type: type[BoilerState] | type[HeatingCircuitMode] | type[HeatingCircuitRegulationState] | type[DoorContactState]
    labels: Mapping[int, str]
    evidence: Literal["firmware", "live_observation"]
    verified_firmware: str | None
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))

    def decode(self, value: object) -> ChannelState | None:
        """Never coerce bool, text, fractional floats, or another family's enum."""
        if type(value) not in (int, float, self.enum_type):
            return None
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            return None
        if not isinstance(value, (int, float)) or value not in self.labels:
            return None
        return self.enum_type(int(value))


_BOILER = ChannelMeaning(
    "KesselZustand", "Boiler state", BoilerState, {
        BoilerState.OFF: "off / ready",
        BoilerState.IGNITION_PREPARATION: "ignition preparation",
        BoilerState.IGNITION_MONITORING: "ignition monitoring",
        BoilerState.IGNITION_PAUSE: "ignition pause",
        BoilerState.TRANSITION_TO_POWER_BURNING: "transition to power burning",
        BoilerState.STARTUP: "startup / heating up",
        BoilerState.POWER_BURNING: "power burning",
        BoilerState.EMBER_MAINTENANCE: "ember maintenance",
        BoilerState.BURNOUT: "burnout",
        BoilerState.DE_ASHING: "de-ashing",
        BoilerState.RESIDUAL_HEAT: "residual heat",
        BoilerState.MANUAL: "manual",
        BoilerState.OVERTEMPERATURE: "overtemperature",
        BoilerState.EMBER_MAINTENANCE_LOCKOUT: "ember-maintenance lockout",
    }, "firmware", "2.7.4",
    "Combustion state, not Wahlschalter operating mode. Shared enum membership "
    "does not establish reachability, heat delivery, or whole-system shutdown.",
)
_DOOR = ChannelMeaning(
    "tuerkontakt", "Boiler door contact", DoorContactState,
    {DoorContactState.OPEN: "open", DoorContactState.CLOSED: "closed"},
    "live_observation", None,
    "0=open, 1=closed observed on 2026-11-20; firmware unspecified. No independent "
    "HTTP polarity confirmation. Open may mean an open door, unplugged contact, "
    "or broken cable. Not a safety interlock or physical-door guarantee.",
)
_MODE_LABELS: Mapping[int, str] = {
    HeatingCircuitMode.OFF: "off",
    HeatingCircuitMode.AUTOMATIC: "automatic",
    HeatingCircuitMode.PERMANENT_REDUCED: "continuous reduced-temperature operation",
    HeatingCircuitMode.PERMANENT_COMFORT: "continuous comfort heating",
    HeatingCircuitMode.ONCE_COMFORT: "one-time comfort heating",
    HeatingCircuitMode.ONCE_REDUCED: "one-time reduced-temperature operation",
}
_REGULATION_LABELS: Mapping[int, str] = {
    HeatingCircuitRegulationState.OFF: "off",
    HeatingCircuitRegulationState.COMFORT: "comfort heating",
    HeatingCircuitRegulationState.REDUCED: "reduced-temperature operation",
    HeatingCircuitRegulationState.FROST_PROTECTION: "frost protection",
    HeatingCircuitRegulationState.DAYTIME_REDUCTION_SHUTDOWN: "off above daytime-reduction outdoor-temperature limit",
    HeatingCircuitRegulationState.NIGHTTIME_REDUCTION_SHUTDOWN: "off above nighttime-reduction outdoor-temperature limit",
    HeatingCircuitRegulationState.OUTDOOR_TEMPERATURE_SHUTDOWN: "off above general outdoor-temperature heating limit",
    HeatingCircuitRegulationState.OVERTEMPERATURE: "overtemperature / heat dissipation",
    HeatingCircuitRegulationState.CHIMNEY_SWEEP: "chimney-sweep / emissions-test operation",
    HeatingCircuitRegulationState.RESIDUAL_HEAT: "residual-heat utilization",
}

# Exact names only: no general Hk*, Boiler*, fault, or global-mode decoder.
CHANNEL_MEANINGS: Mapping[str, ChannelMeaning] = MappingProxyType({
    _BOILER.name: _BOILER,
    _DOOR.name: _DOOR,
    **{f"Hk{zone}Status": ChannelMeaning(
        f"Hk{zone}Status", f"Heating circuit {zone} operating mode",
        HeatingCircuitMode, _MODE_LABELS, "firmware", "2.7.4",
        "Current runtime mode, not the stored command or proof of heat delivery. "
        "One-time expiry is controller-owned, not a fixed-duration client timer.",
    ) for zone in ("1", "2", "A")},
    **{f"Hk{zone}Zustand": ChannelMeaning(
        f"Hk{zone}Zustand", f"Heating circuit {zone} regulation state",
        HeatingCircuitRegulationState, _REGULATION_LABELS, "firmware", "2.7.4",
        "Regulation state, not operating mode or proof of pump/valve operation "
        "or physical heat delivery. Shutdown codes 4/5/6 use HK-12/HK-13/HK-11.",
    ) for zone in ("1", "2", "A")},
})


def get_channel_meaning(name: str) -> ChannelMeaning | None:
    """Exact-name offline lookup; unmapped channels return None."""
    return CHANNEL_MEANINGS.get(name)


def decode_channel_value(name: str, value: object) -> ChannelState | None:
    """Explicit decoding; unknown names/codes or invalid inputs return None.

    This does not modify snapshots, issue requests, certify firmware
    compatibility, or grant writes. Inspect get_channel_meaning for evidence
    and labels, especially before using the observation-based door mapping.
    """
    meaning = get_channel_meaning(name)
    return meaning.decode(value) if meaning is not None else None
