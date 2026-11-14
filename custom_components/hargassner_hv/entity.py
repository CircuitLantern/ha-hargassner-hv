# SPDX-License-Identifier: Apache-2.0
"""Shared device information and state availability."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from hvclient import CIRCUIT_MODE_WRITES, Parameter

from .const import DOMAIN, NAME
from .coordinator import HargassnerConfigEntry, HargassnerCoordinator, mode_zone


class HargassnerEntity(CoordinatorEntity[HargassnerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: HargassnerConfigEntry, coordinator, key: str, name: str):
        super().__init__(coordinator)
        self.entry = entry
        self.runtime = entry.runtime_data
        assert entry.unique_id is not None
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)}, name=NAME,
            manufacturer="Hargassner", model="Smart HV",
            sw_version=self.runtime.info.software_version,
        )

    @property
    def available(self) -> bool:
        return super().available and not self.runtime.closed


class HargassnerParameterEntity(HargassnerEntity):
    def __init__(self, entry: HargassnerConfigEntry, name: str, label: str, *, control=False):
        super().__init__(entry, entry.runtime_data.parameters, f"parameter_{name}", label)
        self.parameter_name = name
        self.control = control

    @property
    def parameter(self) -> Parameter | None:
        return self.runtime.parameter(self.parameter_name)

    @property
    def available(self) -> bool:
        parameter = self.parameter
        if not super().available or parameter is None or self.runtime.closed:
            return False
        if self.parameter_name in CIRCUIT_MODE_WRITES:
            return self.runtime.circuit_present(mode_zone(self.parameter_name))
        return not self.control or parameter.active is True
