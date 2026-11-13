# SPDX-License-Identifier: MPL-2.0
"""Integration setup and actual HA entity/service behavior with synthetic data."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.hargassner_hv.const import DOMAIN


def entity_id(hass, entry, platform, name):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_parameter_{name}")


async def test_default_setup_is_read_only(hass, client, make_entry):
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    boiler = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_channel_kesselfuehler")
    assert hass.states.get(boiler).state == "65.0"
    assert registry.async_get(boiler).entity_category is None
    setting = registry.async_get(entity_id(hass, entry, "sensor", "4"))
    assert setting.entity_category is EntityCategory.DIAGNOSTIC
    assert setting.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(setting.entity_id) is None
    assert entity_id(hass, entry, "number", "4") is None
    assert entity_id(hass, entry, "select", "A1z") is None
    client.async_set_parameter.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    client.async_close.assert_awaited_once()


async def test_opted_in_number_and_mode_call_sdk_once(hass, client, make_entry):
    entry = make_entry(writes=("4", "A1z"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call("number", "set_value", {
        "entity_id": entity_id(hass, entry, "number", "4"), "value": 21,
    }, blocking=True)
    client.async_set_parameter.assert_awaited_once_with("4", 21, expected=20.5)
    assert hass.states.get(entity_id(hass, entry, "number", "4")).state == "21.0"
    await hass.services.async_call("select", "select_option", {
        "entity_id": entity_id(hass, entry, "select", "A1z"), "option": "automatic",
    }, blocking=True)
    assert client.async_set_parameter.await_count == 2
    assert client.async_set_parameter.await_args.kwargs == {"expected": 3}
    assert hass.states.get(entity_id(hass, entry, "select", "A1z")).state == "automatic"


async def test_config_flow_reads_identity_and_closes_probe(hass, client):
    with patch("custom_components.hargassner_hv.async_setup_entry", new_callable=AsyncMock):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={
            "host": "boiler.example", "port": 80,
        })
    assert result["type"] == "create_entry"
    assert result["data"] == {"host": "boiler.example", "port": 80, "identity_source": "mac"}
    client.async_get_info.assert_awaited_once()
    client.async_close.assert_awaited_once()
    client.async_set_parameter.assert_not_awaited()
