# SPDX-License-Identifier: MPL-2.0
"""Read-only parameter state and explicitly enabled raw diagnostics."""

from dataclasses import asdict

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import callback

from hvclient import (
    CIRCUIT_MODE_WRITES,
    CIRCUIT_SCHEDULE_WRITES,
    NUMERIC_USER_WRITES,
    Choice,
    HeatingCircuitMode,
)

from .const import CONTROL_LABELS
from .entity import HargassnerEntity, HargassnerParameterEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    known_parameters = set()
    known_channels = set()

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
                known_channels.add(name)
                entities.append(RawChannelSensor(entry, name))
        if entities:
            async_add_entities(entities)

    add_parameters()
    async_add_entities([FirmwareSensor(entry)])
    entry.async_on_unload(runtime.parameters.async_add_listener(add_parameters))
    if runtime.raw_channels:
        add_channels()
        entry.async_on_unload(runtime.channels.async_add_listener(add_channels))
    if runtime.pcb is not None:
        async_add_entities([PcbSensor(entry, "internal_temperature", "PCB temperature (raw)"),
                            PcbSensor(entry, "internal_voltage", "PCB voltage (raw)")])


class ParameterSensor(HargassnerParameterEntity, SensorEntity):
    def __init__(self, entry, name, label):
        super().__init__(entry, name, label)
        parameter = self.parameter
        if name in CIRCUIT_MODE_WRITES:
            self._attr_entity_registry_enabled_default = self.runtime.circuit_present(parameter.meaning.zone)
        else:
            self._attr_entity_registry_enabled_default = parameter.active is True
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


class PcbSensor(HargassnerEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry, field, label):
        super().__init__(entry, entry.runtime_data.pcb, field, label)
        self.field = field

    @property
    def native_value(self):
        return getattr(self.coordinator.data, self.field) if self.coordinator.data is not None else None
