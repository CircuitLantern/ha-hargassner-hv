# SPDX-License-Identifier: Apache-2.0
"""A dedicated, device-targeted full-program action; no arbitrary write tunnel."""

from dataclasses import asdict

import probatio as vol
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from hvclient import (
    CIRCUIT_SCHEDULE_PARAMETERS,
    DailySchedule,
    ValidationError,
    WeeklySchedule,
)

from .const import DOMAIN

SCHEMA = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("circuit"): vol.In(("1", "2", "A")),
    vol.Required("kind"): vol.In(("daily", "weekly")),
    vol.Required("program"): dict,
    vol.Optional("expected"): dict,
    vol.Optional("preview_only", default=True): cv.boolean,
})


def program_value(kind, value):
    key = "time_ranges" if kind == "daily" else "groups"
    if not isinstance(value, dict) or set(value) != {key}:
        raise ValidationError("Program object has unexpected fields")
    schedule = DailySchedule(value[key]) if kind == "daily" else WeeklySchedule(value[key])
    return schedule.to_json()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def set_program(call):
        _, entry = service.async_get_device_and_config_entry(
            hass, DOMAIN, call.data["device_id"], include_child_devices=False,
        )
        kind = call.data["kind"]
        program = getattr(CIRCUIT_SCHEDULE_PARAMETERS[call.data["circuit"]], kind)
        preview = call.data["preview_only"]
        if not preview and "expected" not in call.data:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="expected_required")
        try:
            requested = program_value(kind, call.data["program"])
            expected = (program_value(kind, call.data["expected"])
                        if "expected" in call.data else None)
        except ValidationError:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_program") from None
        kwargs = {"expected": expected} if expected is not None else {}
        result = await entry.runtime_data.async_control(program.name, requested, preview=preview, **kwargs)
        if not call.return_response:
            return None
        if preview:
            return {"outcome": "preview", "circuit": program.zone, "kind": kind,
                    "requested": result.requested.to_json(),
                    "current": result.parameter.to_dict()["value"], "limits": asdict(program.limits)}
        return {"outcome": result.outcome, "circuit": program.zone, "kind": kind,
                "read_back_matches": result.read_back_matches}

    service.async_register_admin_service(
        hass, DOMAIN, "set_circuit_program", set_program, schema=SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
