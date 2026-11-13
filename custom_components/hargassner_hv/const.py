# SPDX-License-Identifier: MPL-2.0
"""Integration constants and presentation labels, not protocol implementations."""

from hvclient import CIRCUIT_MODE_PARAMETERS, CIRCUIT_SCHEDULE_PARAMETERS

DOMAIN = "hargassner_hv"
NAME = "Hargassner Smart HV"
VERSION = "0.3.0"
CONF_ENABLED_WRITES = "enabled_writes"
CONF_ACK_WRITES = "acknowledge_writes"
CONF_RAW_CHANNELS = "raw_channels"
CONF_PCB = "pcb_diagnostics"
CONF_IDENTITY_SOURCE = "identity_source"

NUMERIC_LABELS = {
    "4": "Circuit 1 comfort room target", "5": "Circuit 1 reduced room target",
    "7": "Circuit 2 comfort room target", "8": "Circuit 2 reduced room target",
    "HP4": "Circuit A comfort room target", "HP5": "Circuit A reduced room target",
    "2": "DHW cylinder 1 target", "HP2": "DHW cylinder A target",
    "11": "Outdoor shutdown threshold",
    "12": "Outdoor shutdown threshold during day setback",
    "13": "Outdoor shutdown threshold during night setback",
    "A2": "Circuit 1 radiator slope", "A2a": "Circuit 1 underfloor slope",
    "A12": "Circuit 2 radiator slope", "A12a": "Circuit 2 underfloor slope",
    "A62": "Circuit A radiator slope", "A62a": "Circuit A underfloor slope",
}
CONTROL_LABELS = {
    **NUMERIC_LABELS,
    **{definition.name: definition.label for definition in CIRCUIT_MODE_PARAMETERS.values()},
    **{program.name: program.label for circuit in CIRCUIT_SCHEDULE_PARAMETERS.values()
       for program in (circuit.daily, circuit.weekly)},
}
