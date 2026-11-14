# SPDX-License-Identifier: Apache-2.0
"""Synthetic SDK responses; all HA tests disable non-loopback network access."""

import copy
import shutil
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ThreadedResolver
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    floor_registry,
    label_registry,
)
from homeassistant.setup import async_setup_component

from custom_components.hargassner_hv.const import (
    CONF_ACK_WRITES,
    CONF_ENABLED_WRITES,
    CONF_IDENTITY_SOURCE,
    DOMAIN,
)
from custom_components.hargassner_hv.hvclient import (
    PARAMETER_WRITES,
    AsyncHargassnerClient,
    WriteResult,
)
from custom_components.hargassner_hv.hvclient.models import (
    channels_from_json,
    parameter_from_json,
    pcb_info_from_json,
    system_info_from_json,
)
from custom_components.hargassner_hv.identity import identity
from tests.library_support import schedule_catalog, user_catalog


@pytest.fixture
async def hass(tmp_path, monkeypatch):
    # Keep HA's managed session real without initializing multicast/network
    # discovery. SDK calls are mocked, or use numeric loopback addresses.
    class TestResolver(ThreadedResolver):
        async def real_close(self):
            await super().close()

    monkeypatch.setattr("homeassistant.helpers.aiohttp_client._async_make_resolver",
                        lambda hass: TestResolver())
    source = Path(__file__).resolve().parents[2] / "custom_components"
    shutil.copytree(source, tmp_path / "custom_components", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "custom_components/__init__.py").touch()
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    loader.async_setup(instance)
    device_registry.async_setup(instance)
    for registry in (floor_registry, area_registry, label_registry, device_registry, entity_registry):
        await registry.async_load(instance)
    assert await async_setup_component(instance, "homeassistant", {})
    yield instance
    await instance.async_stop(force=True)
    await instance.async_block_till_done()


@pytest.fixture
def client():
    api = AsyncMock(spec=AsyncHargassnerClient)
    api.catalog = {**user_catalog(), **schedule_catalog()}
    api.info = system_info_from_json({
        "softwareVersion": "smart_hv_3.4.0", "mac": "02:00:00:00:00:01",
        "systemId": "synthetic-system", "commissionNumber": "synthetic-commission",
        "articleSerialNumber": "synthetic-serial", "private_extension": "synthetic-secret",
    })
    api.async_get_info.return_value = api.info
    api.async_get_channels.return_value = channels_from_json({
        "synthetic.raw": 0, "synthetic.temperature": 42.5,
        "kesselfuehler": 65.0, "aussentemperatur": 4.5,
        "puffer_oben": 60.0, "Hk1Fr35RaumTemp": 21.25,
    })
    api.async_get_pcb.return_value = pcb_info_from_json({"internalTemperature": 26.5, "internalVoltage": 24.0})

    async def parameters(names=None):
        selected = api.catalog if names is None else {name: api.catalog[name] for name in names}
        return {name: parameter_from_json(copy.deepcopy(row)) for name, row in selected.items()
                if row["securityLevel"] == "USER" or name in PARAMETER_WRITES}

    async def change(name, value, *, expected):
        before = (await parameters([name]))[name]
        api.catalog[name]["value"] = ({"id": int(value), "name": "Synthetic label"}
                                      if name.endswith("z") else copy.deepcopy(value))
        after = (await parameters([name]))[name]
        return WriteResult(name, value, before, after, after, "verified", 200)

    api.async_get_parameters.side_effect = parameters
    api.async_set_parameter.side_effect = change
    api.__aenter__.return_value = api

    async def exit_context(*args):
        await api.async_close()

    api.__aexit__.side_effect = exit_context
    with (
        patch("custom_components.hargassner_hv.AsyncHargassnerClient", return_value=api),
        patch("custom_components.hargassner_hv.config_flow.AsyncHargassnerClient", return_value=api),
    ):
        yield api


@pytest.fixture
def make_entry(hass, client):
    def create(*, writes=(), options=None):
        source, key = identity(client.info)
        entry = ConfigEntry(
            domain=DOMAIN, title="Synthetic controller", unique_id=key,
            data={"host": "boiler.example", "port": 80, CONF_IDENTITY_SOURCE: source},
            options={CONF_ENABLED_WRITES: list(writes), CONF_ACK_WRITES: bool(writes), **(options or {})},
            source="user", version=1, minor_version=1,
            discovery_keys=MappingProxyType({}), subentries_data=None,
        )
        # A real, initially unloaded entry; production code uses public APIs.
        hass.config_entries._entries[entry.entry_id] = entry
        return entry
    return create
