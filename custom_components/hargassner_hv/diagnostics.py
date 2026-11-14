# SPDX-License-Identifier: Apache-2.0
"""Whitelisted health diagnostics without configuration, identity, or plant values."""

import re

from .const import VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    version = runtime.info.software_version or ""
    return {
        "integration_version": VERSION,
        "firmware_version": version if re.fullmatch(r"smart_hv_\d+\.\d+\.\d+", version) else "unreported",
        "safety_policy": {"service_mode_initiating_calls": "excluded"},
        "updates": {
            "parameters_last_update_ok": runtime.parameters.last_update_success,
            "channels_last_update_ok": runtime.channels.last_update_success,
            "pcb_enabled": runtime.pcb is not None,
            "pcb_last_update_ok": runtime.pcb.last_update_success if runtime.pcb is not None else None,
        },
        "controls_enabled": bool(runtime.enabled_writes),
        "raw_channels_enabled": runtime.raw_channels,
        "client_closed": runtime.closed,
    }
