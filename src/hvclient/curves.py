# SPDX-License-Identifier: MPL-2.0

"""Reviewed heating-curve names and circuit/profile relationships, not general installer access."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class HeatingCurveProfile:
    zone: str
    selector: str
    choice_id: int
    peer: str


# 2.7.4 control initialization: circuit type 1 -> radiator, 2 -> underfloor.
# In particular, translated A12/A12a help text must not decide the profile.
HEATING_CURVES = MappingProxyType({
    "A2": HeatingCurveProfile("1", "A1", 1, "A2a"),
    "A2a": HeatingCurveProfile("1", "A1", 2, "A2"),
    "A12": HeatingCurveProfile("2", "A11", 1, "A12a"),
    "A12a": HeatingCurveProfile("2", "A11", 2, "A12"),
    "A62": HeatingCurveProfile("A", "A61", 1, "A62a"),
    "A62a": HeatingCurveProfile("A", "A61", 2, "A62"),
})
