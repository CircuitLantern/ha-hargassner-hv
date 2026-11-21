# SPDX-License-Identifier: Apache-2.0
"""Operational readings, diagnostic setting readbacks, and optional raw channels."""

from dataclasses import asdict

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import CONTROL_LABELS, DOMAIN
from .entity import HargassnerEntity, HargassnerParameterEntity
from .hvclient import (
    CIRCUIT_SCHEDULE_WRITES,
    NUMERIC_USER_WRITES,
    Choice,
    HeatingCircuitMode,
)
from .telemetry import OPERATIONAL_CHANNELS, OperationalChannel

PARALLEL_UPDATES = 0

# KesselState::operator QString in firmware 2.7.4; see docs/channel-guide.md.
# Shared enum membership does not imply every state is used by Smart HV.
BOILER_STATES = {
    0: "off / ready",
    1: "ignition preparation",
    2: "ignition monitoring",
    3: "ignition pause",
    4: "transition to power burning",
    5: "startup / heating up",
    6: "power burning",
    7: "ember maintenance",
    8: "burnout",
    9: "de-ashing",
    10: "residual heat",
    11: "manual",
    12: "overtemperature",
    13: "ember-maintenance lockout",
}

# Status is HeizkreisState (selected runtime mode); Zustand is the separate
# Heizkreis::HeizkreisZustand regulation enum. Evidence: docs/channel-guide.md.
HEATING_CIRCUIT_MODES = {
    0: "off",
    1: "automatic",
    2: "continuous reduced-temperature operation",
    3: "continuous comfort heating",
    4: "one-time comfort heating",
    5: "one-time reduced-temperature operation",
}
HEATING_CIRCUIT_STATES = {
    0: "off",
    1: "comfort heating",
    2: "reduced-temperature operation",
    3: "frost protection",
    4: "off above daytime-reduction outdoor-temperature limit",
    5: "off above nighttime-reduction outdoor-temperature limit",
    6: "off above general outdoor-temperature heating limit",
    7: "overtemperature / heat dissipation",
    8: "chimney-sweep / emissions-test operation",
    9: "residual-heat utilization",
}
HEATING_CIRCUIT_DIAGNOSTICS = {
    f"Hk{circuit}{suffix}": (f"Heating circuit {circuit} {label}", meanings)
    for circuit in ("1", "2", "A")
    for suffix, label, meanings in (
        ("Status", "operating mode", HEATING_CIRCUIT_MODES),
        ("Zustand", "regulation state", HEATING_CIRCUIT_STATES),
    )
}


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    known_parameters = set()
    known_channels = set()
    registry = er.async_get(hass)

    @callback
    def add_parameters():
        entities = []
        for name, label in CONTROL_LABELS.items():
            parameter = runtime.parameter(name)
            if parameter is not None and name not in known_parameters:
                known_parameters.add(name)
                entities.append(ParameterSensor(entry, name, label))
        if entities:
            async_add_entities(entities)

    @callback
    def add_channels():
        if runtime.channels.data is None:
            return
        entities = []
        for name in runtime.channels.data:
            if name not in known_channels:
                if name in OPERATIONAL_CHANNELS:
                    known_channels.add(name)
                    entity = OperationalChannelSensor(entry, OPERATIONAL_CHANNELS[name])
                    assert entity.unique_id is not None
                    old_id = registry.async_get_entity_id("sensor", DOMAIN, entity.unique_id)
                    old = registry.async_get(old_id) if old_id is not None else None
                    if (old is not None and old.disabled_by is er.RegistryEntryDisabler.INTEGRATION
                            and old.original_name == f"Raw {name}"
                            and entity.entity_registry_enabled_default
                            and not entry.pref_disable_new_entities):
                        # Promote only the old integration-hidden default. User
                        # disables, names, IDs, and global preferences survive.
                        registry.async_update_entity(old.entity_id, disabled_by=None)
                    entities.append(entity)
                elif runtime.raw_channels:
                    known_channels.add(name)
                    if name == "tuerkontakt":
                        entities.append(DoorContactSensor(entry))
                    elif name == "KesselZustand":
                        entities.append(BoilerStateSensor(entry))
                    elif name in HEATING_CIRCUIT_DIAGNOSTICS:
                        entities.append(HeatingCircuitDiagnosticSensor(entry, name))
                    else:
                        entities.append(RawChannelSensor(entry, name))
        if entities:
            async_add_entities(entities)

    add_parameters()
    async_add_entities([FirmwareSensor(entry)])
    entry.async_on_unload(runtime.parameters.async_add_listener(add_parameters))
    # Operational telemetry is a primary feature, independent of the advanced
    # raw-diagnostics option. Also discover channels appearing after startup.
    add_channels()
    entry.async_on_unload(runtime.channels.async_add_listener(add_channels))
    if runtime.pcb is not None:
        async_add_entities([PcbSensor(entry, "internal_temperature", "PCB temperature (raw)"),
                            PcbSensor(entry, "internal_voltage", "PCB voltage (raw)")])


class ParameterSensor(HargassnerParameterEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry, name, label):
        super().__init__(entry, name, f"{label} (setting readback)")
        parameter = self.parameter
        if name in NUMERIC_USER_WRITES and parameter.unit == "°C":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def available(self):
        if not super().available:
            return False
        if self.parameter_name in NUMERIC_USER_WRITES:
            return (self.parameter.unit == "°C"
                    and self.parameter.type == NUMERIC_USER_WRITES[self.parameter_name])
        return True

    @property
    def native_value(self):
        parameter = self.parameter
        if parameter is None:
            return None
        if self.parameter_name in CIRCUIT_SCHEDULE_WRITES:
            return "active" if parameter.active is True else "inactive"
        if isinstance(parameter.value, Choice):
            try:
                return HeatingCircuitMode(parameter.value.id).name.lower()
            except ValueError:
                return None
        return parameter.value if type(parameter.value) in (int, float) else None

    @property
    def extra_state_attributes(self):
        parameter = self.parameter
        if parameter is None:
            return None
        attributes = {"parameter": self.parameter_name, "active": parameter.active}
        if self.parameter_name in CIRCUIT_SCHEDULE_WRITES:
            attributes.update(program=parameter.to_dict()["value"], limits=asdict(parameter.schedule_limits))
        elif isinstance(parameter.value, Choice):
            attributes["mode_id"] = parameter.value.id
        else:
            attributes.update(minimum=parameter.minimum, maximum=parameter.maximum, increment=parameter.increment)
        return attributes


class FirmwareSensor(HargassnerEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry):
        super().__init__(entry, entry.runtime_data.parameters, "firmware", "Firmware version")

    @property
    def native_value(self):
        version = self.runtime.info.software_version
        return version if version is not None and len(version) <= 255 else None


class RawChannelSensor(HargassnerEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry, name):
        super().__init__(entry, entry.runtime_data.channels, f"channel_{name}", f"Raw {name}")
        self.channel = name

    @property
    def available(self):
        return super().available and self.coordinator.data is not None and self.channel in self.coordinator.data

    @property
    def native_value(self):
        return self.coordinator.data.get(self.channel) if self.coordinator.data is not None else None


class DoorContactSensor(RawChannelSensor):
    """Observed door-contact polarity; retain the existing diagnostic identity."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["open", "closed"]
    _attr_icon = "mdi:door"

    def __init__(self, entry):
        super().__init__(entry, "tuerkontakt")
        self._attr_name = "Boiler door contact"

    @property
    def available(self):
        return super().available and super().native_value in (0, 1)

    @property
    def native_value(self):
        if not self.available:
            return None
        # Live-device observation, not a documented HTTP API polarity contract.
        return "open" if super().native_value == 0 else "closed"

    @property
    def extra_state_attributes(self):
        return {"channel": self.channel, "raw_value": super().native_value}


class BoilerStateSensor(RawChannelSensor):
    """Firmware-defined combustion states; preserve the raw diagnostic identity."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(BOILER_STATES.values())
    _attr_icon = "mdi:fire"

    def __init__(self, entry):
        super().__init__(entry, "KesselZustand")
        self._attr_name = "Boiler state"

    @property
    def available(self):
        return super().available and super().native_value in BOILER_STATES

    @property
    def native_value(self):
        return BOILER_STATES.get(super().native_value) if self.available else None

    @property
    def extra_state_attributes(self):
        return {"channel": self.channel, "raw_value": super().native_value}


class HeatingCircuitDiagnosticSensor(RawChannelSensor):
    """Exact-name runtime mode/state decoding, independent of climate controls."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:radiator"

    def __init__(self, entry, name):
        super().__init__(entry, name)
        self._attr_name, self.meanings = HEATING_CIRCUIT_DIAGNOSTICS[name]
        self._attr_options = list(self.meanings.values())

    @property
    def available(self):
        return super().available and super().native_value in self.meanings

    @property
    def native_value(self):
        return self.meanings.get(super().native_value) if self.available else None

    @property
    def extra_state_attributes(self):
        return {"channel": self.channel, "raw_value": super().native_value}


class OperationalChannelSensor(HargassnerEntity, SensorEntity):
    """One reviewed runtime channel, retaining the previous raw entity identity."""

    def __init__(self, entry, description: OperationalChannel):
        super().__init__(entry, entry.runtime_data.channels, f"channel_{description.key}", description.name)
        self.description = description
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        configured = description.circuit is None or self.runtime.circuit_present(description.circuit)
        if description.default_active_parameter is not None:
            parameter = self.runtime.parameter(description.default_active_parameter)
            configured = configured and parameter is not None and parameter.active is True
        self._attr_entity_registry_enabled_default = self.available and configured

    @property
    def available(self):
        if not super().available or self.coordinator.data is None:
            return False
        return (self.description.key in self.coordinator.data
                and self.coordinator.data[self.description.key] not in self.description.unavailable_values)

    @property
    def native_value(self):
        if not self.available:
            return None
        return self.coordinator.data[self.description.key]

    @property
    def extra_state_attributes(self):
        return {"channel": self.description.key}


class PcbSensor(HargassnerEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry, field, label):
        super().__init__(entry, entry.runtime_data.pcb, field, label)
        self.field = field

    @property
    def native_value(self):
        return getattr(self.coordinator.data, self.field) if self.coordinator.data is not None else None
