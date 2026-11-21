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
    CHANNEL_MEANINGS,
    CIRCUIT_SCHEDULE_WRITES,
    NUMERIC_USER_WRITES,
    Choice,
    HeatingCircuitMode,
)
from .telemetry import OPERATIONAL_CHANNELS, OperationalChannel

PARALLEL_UPDATES = 0


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
                    elif name in CHANNEL_MEANINGS:
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


class EnumChannelSensor(RawChannelSensor):
    """SDK-defined meanings with HA-specific availability and raw identity."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, entry, name):
        super().__init__(entry, name)
        self.meaning = CHANNEL_MEANINGS[name]
        self._attr_name = self.meaning.label
        self._attr_options = list(self.meaning.labels.values())

    @property
    def available(self):
        return super().available and self.meaning.decode(super().native_value) is not None

    @property
    def native_value(self):
        if not self.available:
            return None
        decoded = self.meaning.decode(super().native_value)
        return self.meaning.labels[decoded] if decoded is not None else None

    @property
    def extra_state_attributes(self):
        return {"channel": self.channel, "raw_value": super().native_value}


class DoorContactSensor(EnumChannelSensor):
    """Observation-based door-contact interpretation, not a safety guarantee."""

    _attr_icon = "mdi:door"

    def __init__(self, entry):
        super().__init__(entry, "tuerkontakt")


class BoilerStateSensor(EnumChannelSensor):
    """Firmware-defined combustion states; preserve the raw diagnostic identity."""

    _attr_icon = "mdi:fire"

    def __init__(self, entry):
        super().__init__(entry, "KesselZustand")


class HeatingCircuitDiagnosticSensor(EnumChannelSensor):
    """Exact-name runtime mode/state decoding, independent of climate controls."""

    _attr_icon = "mdi:radiator"


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
