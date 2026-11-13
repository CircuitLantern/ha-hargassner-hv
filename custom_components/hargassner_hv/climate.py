# SPDX-License-Identifier: MPL-2.0
"""Circuit climate interfaces; regulation and one-time expiry stay controller-owned."""

import math
from fractions import Fraction
from types import MappingProxyType

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    PRESET_NONE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError

from hvclient import (
    CIRCUIT_MODE_PARAMETERS,
    HeatingCircuitMode,
    Parameter,
    ValidationError,
    WritePolicy,
)
from hvclient.policy import validate_change, validate_mode_parameter

from .const import DOMAIN
from .entity import HargassnerEntity

PARALLEL_UPDATES = 1
PRESETS = MappingProxyType({
    mode.name.lower(): mode for mode in HeatingCircuitMode if mode.value >= 2
})
# Exact reviewed target names, not localized labels or calculated flow targets.
TARGETS = MappingProxyType({"1": ("4", "5"), "2": ("7", "8"), "A": ("HP4", "HP5")})
COMFORT = frozenset({HeatingCircuitMode.PERMANENT_COMFORT, HeatingCircuitMode.ONCE_COMFORT})


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    known = set()

    @callback
    def add_circuits():
        entities = []
        for zone, definition in CIRCUIT_MODE_PARAMETERS.items():
            if (zone not in known and definition.name in runtime.enabled_writes
                    and runtime.parameter(definition.name) is not None
                    and runtime.circuit_present(zone)):
                known.add(zone)
                entities.append(HargassnerClimate(entry, zone))
        if entities:
            async_add_entities(entities)

    add_circuits()
    entry.async_on_unload(runtime.parameters.async_add_listener(add_circuits))


class HargassnerClimate(HargassnerEntity, ClimateEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "circuit"

    def __init__(self, entry, zone):
        super().__init__(entry, entry.runtime_data.parameters, f"climate_{zone}", f"Circuit {zone}")
        self.zone = zone
        self.mode_parameter = CIRCUIT_MODE_PARAMETERS[zone].name
        self.mode_channel = f"Hk{zone}Status"
        self.room_channel = f"Hk{zone}Fr35RaumTemp"
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT]
        self._attr_preset_modes = [PRESET_NONE, *PRESETS]

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.channels.async_add_listener(self._handle_coordinator_update))

    @property
    def mode(self) -> HeatingCircuitMode | None:
        runtime = self.runtime
        parameter = runtime.parameter(self.mode_parameter)
        if (runtime.closed or not runtime.parameters.last_update_success
                or parameter is None or not runtime.circuit_present(self.zone)
                or self.mode_parameter not in runtime.enabled_writes):
            return None
        try:
            validate_mode_parameter(parameter)
        except ValidationError:
            return None
        # Parameter readback may retain the last command after runtime expiry.
        # Require a healthy exact runtime channel; never guess from that setting.
        channels = runtime.channels
        if not channels.last_update_success or channels.data is None:
            return None
        value = channels.data.get(self.mode_channel)
        if type(value) not in (int, float) or not math.isfinite(value) or value not in range(6):
            return None
        verified = runtime.mode_readbacks.get(self.mode_parameter)
        if verified is not None and channels.observed_at <= verified[1]:
            value = verified[0]
        return HeatingCircuitMode(int(value))

    @property
    def available(self):
        return super().available and self.mode is not None

    @property
    def hvac_mode(self):
        mode = self.mode
        if mode is None:
            return None
        return HVACMode.OFF if mode == HeatingCircuitMode.OFF else (
            HVACMode.AUTO if mode == HeatingCircuitMode.AUTOMATIC else HVACMode.HEAT
        )

    @property
    def preset_mode(self):
        mode = self.mode
        return None if mode is None else mode.name.lower() if mode.value >= 2 else PRESET_NONE

    @property
    def native_current_temperature(self):
        channels = self.runtime.channels
        value = channels.data.get(self.room_channel) if channels.data is not None else None
        return (value if channels.last_update_success and type(value) in (int, float)
                and math.isfinite(value) and value != -1 else None)

    @property
    def target_parameter(self) -> Parameter | None:
        mode = self.mode
        if mode is None or mode.value < 2:
            return None
        name = TARGETS[self.zone][0 if mode in COMFORT else 1]
        parameter = self.runtime.parameter(name)
        if parameter is None or name not in self.runtime.enabled_writes:
            return None
        try:
            # Use the SDK's reviewed metadata/grid policy, without transport.
            validate_change(parameter, parameter.value, WritePolicy.only(name))
        except ValidationError:
            return None
        return parameter

    @property
    def supported_features(self):
        features = (ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.TURN_ON
                    | ClimateEntityFeature.TURN_OFF)
        if self.target_parameter is not None:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def native_target_temperature(self):
        parameter = self.target_parameter
        return parameter.value if parameter is not None else None

    @property
    def min_temp(self):
        parameter = self.target_parameter
        return parameter.minimum if parameter is not None else 7

    @property
    def max_temp(self):
        parameter = self.target_parameter
        return parameter.maximum if parameter is not None else 35

    @property
    def target_temperature_step(self):
        parameter = self.target_parameter
        return parameter.increment if parameter is not None else None

    def _refuse(self):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="change_refused")

    async def _guard_mode(self, snapshot):
        # Runs under the shared number/select/program/climate control lock, after
        # identity verification. Never reroute a queued target after mode change.
        if self.mode != snapshot:
            self._refuse()
        await self.runtime.channels.async_refresh()
        if self.mode is None or self.mode != snapshot:
            self._refuse()

    async def _set_mode(self, requested):
        snapshot = self.mode
        if snapshot is None:
            self._refuse()
        if requested == snapshot:
            return  # No rearming/replay of an already-selected one-time mode.
        # The SDK deliberately skips an unchanged stored parameter. If runtime
        # differs but the requested ID is already stored, that is not proof of
        # a runtime transition; refuse rather than force/replay a mode write.
        if self.runtime.parameter(self.mode_parameter).value.id == requested:
            self._refuse()
        await self.runtime.async_control(
            self.mode_parameter, requested, guard=lambda: self._guard_mode(snapshot),
        )

    async def async_set_hvac_mode(self, hvac_mode):
        mode = self.mode
        if hvac_mode == HVACMode.OFF:
            requested = HeatingCircuitMode.OFF
        elif hvac_mode == HVACMode.AUTO:
            requested = HeatingCircuitMode.AUTOMATIC
        elif hvac_mode == HVACMode.HEAT and mode is not None:
            requested = mode if mode.value >= 2 else HeatingCircuitMode.PERMANENT_COMFORT
        else:
            self._refuse()
        await self._set_mode(requested)

    async def async_set_preset_mode(self, preset_mode):
        if preset_mode == PRESET_NONE:
            mode = self.mode
            if mode is None:
                self._refuse()
            requested = mode if mode.value < 2 else HeatingCircuitMode.AUTOMATIC
        elif preset_mode in PRESETS:
            requested = PRESETS[preset_mode]
        else:
            self._refuse()
        await self._set_mode(requested)

    async def async_turn_on(self):
        await self._set_mode(HeatingCircuitMode.AUTOMATIC)

    async def async_turn_off(self):
        await self._set_mode(HeatingCircuitMode.OFF)

    async def async_set_temperature(self, **kwargs):
        # HA 2026.11 forwards standard service targeting fields alongside the
        # converted temperatures. They are routing metadata, not boiler controls.
        kwargs = {key: value for key, value in kwargs.items()
                  if key not in {"entity_id", "device_id", "area_id", "floor_id", "label_id"}}
        if set(kwargs) - {ATTR_TEMPERATURE, "hvac_mode"} or ATTR_TEMPERATURE not in kwargs:
            self._refuse()
        if "hvac_mode" in kwargs and kwargs["hvac_mode"] != self.hvac_mode:
            self._refuse()  # Reject multi-write actions before any POST.
        snapshot = self.mode
        parameter = self.target_parameter
        value = kwargs[ATTR_TEMPERATURE]
        if parameter is None or type(value) not in (int, float) or not math.isfinite(value):
            self._refuse()
        # HA's Fahrenheit conversion can turn 69.8 °F into
        # 20.999999999999996 °C. Correct only machine-roundoff near an exact
        # metadata grid point, never round a genuinely off-grid user target.
        low, step = Fraction(str(parameter.minimum)), Fraction(str(parameter.increment))
        nearest = float(low + round((Fraction(str(value)) - low) / step) * step)
        tolerance = min(float(step) * 1e-9, 8 * max(math.ulp(float(value)), math.ulp(nearest)))
        if abs(value - nearest) <= tolerance:
            value = nearest
        try:
            value = validate_change(parameter, value, WritePolicy.only(parameter.name))
        except ValidationError:
            self._refuse()

        async def guard():
            await self._guard_mode(snapshot)
            current = self.target_parameter
            if current is None or current.name != parameter.name:
                self._refuse()

        await self.runtime.async_control(parameter.name, value, expected=parameter.value, guard=guard)
