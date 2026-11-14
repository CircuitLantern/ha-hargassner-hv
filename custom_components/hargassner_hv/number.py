# SPDX-License-Identifier: Apache-2.0
"""Explicitly opted-in temperature and slope controls."""

import math

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.exceptions import ServiceValidationError

from hvclient import NUMERIC_USER_WRITES, NUMERIC_WRITES

from .const import DOMAIN, NUMERIC_LABELS
from .entity import HargassnerParameterEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        HargassnerNumber(entry, name, NUMERIC_LABELS[name])
        for name in NUMERIC_WRITES
        if name in entry.runtime_data.enabled_writes and entry.runtime_data.parameter(name) is not None
    ])


class HargassnerNumber(HargassnerParameterEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, name, label):
        super().__init__(entry, name, label, control=True)
        if name in NUMERIC_USER_WRITES:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def available(self):
        parameter = self.parameter
        return (super().available and parameter.type == NUMERIC_WRITES[self.parameter_name]
                and parameter.unit == ("°C" if self.parameter_name in NUMERIC_USER_WRITES else "")
                and all(type(value) in (int, float) and math.isfinite(value) for value in
                        (parameter.minimum, parameter.maximum, parameter.increment, parameter.value))
                and parameter.minimum <= parameter.maximum and parameter.increment > 0)

    @property
    def native_value(self):
        return self.parameter.value if self.parameter is not None else None

    @property
    def native_min_value(self):
        return self.parameter.minimum if self.parameter is not None and self.parameter.minimum is not None else 0

    @property
    def native_max_value(self):
        return self.parameter.maximum if self.parameter is not None and self.parameter.maximum is not None else 1

    @property
    def native_step(self):
        return self.parameter.increment if self.parameter is not None and self.parameter.increment is not None else 1

    async def async_set_native_value(self, value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="change_refused")
        if self.parameter is not None and self.parameter.type == "INTEGER":
            if value != int(value):
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="change_refused")
            value = int(value)
        await self.runtime.async_control(self.parameter_name, value)
