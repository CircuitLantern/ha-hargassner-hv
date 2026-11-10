# SPDX-License-Identifier: MPL-2.0
"""Explicitly opted-in circuit modes, never boiler-wide modes."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError

from hvclient import CIRCUIT_MODE_PARAMETERS, Choice, HeatingCircuitMode

from .const import DOMAIN
from .entity import HargassnerParameterEntity

PARALLEL_UPDATES = 1
MODES = {mode.name.lower(): mode for mode in HeatingCircuitMode}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        HargassnerMode(entry, definition.name, definition.label)
        for definition in CIRCUIT_MODE_PARAMETERS.values()
        if definition.name in entry.runtime_data.enabled_writes
        and entry.runtime_data.parameter(definition.name) is not None
    ])


class HargassnerMode(HargassnerParameterEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "circuit_mode"

    def __init__(self, entry, name, label):
        super().__init__(entry, name, label, control=True)
        self._attr_options = list(MODES)

    @property
    def available(self):
        parameter = self.parameter
        return (super().available and parameter.security_level == "DEVELOPER"
                and parameter.type == "CHOICE" and parameter.active is False
                and isinstance(parameter.value, Choice)
                and parameter.value.id in range(6) and parameter.unit in (None, "")
                and len(parameter.choices) == 6
                and {choice.id for choice in parameter.choices} == set(range(6)))

    @property
    def current_option(self):
        parameter = self.parameter
        if parameter is None or not isinstance(parameter.value, Choice):
            return None
        try:
            return HeatingCircuitMode(parameter.value.id).name.lower()
        except ValueError:
            return None

    async def async_select_option(self, option):
        if option not in MODES:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="change_refused")
        await self.runtime.async_control(self.parameter_name, MODES[option])
