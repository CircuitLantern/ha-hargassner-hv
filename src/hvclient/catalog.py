# SPDX-License-Identifier: Apache-2.0

"""Catalog scope: USER plus explicit curve/mode exceptions, never whole levels."""

from typing import Any

from .curves import HEATING_CURVES
from .modes import CIRCUIT_MODES


def supported_parameter(name: Any, security_level: Any) -> bool:
    return security_level == "USER" or (
        isinstance(name, str) and (
            (security_level == "PLUMBER" and name in HEATING_CURVES)
            or (security_level == "DEVELOPER" and name in CIRCUIT_MODES)
        )
    )
