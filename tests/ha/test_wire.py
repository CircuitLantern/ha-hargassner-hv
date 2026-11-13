# SPDX-License-Identifier: MPL-2.0
"""HA -> independently installed SDK -> synthetic loopback HTTP device."""

import copy
from unittest.mock import patch

import pytest
from aiohttp import web
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.hargassner_hv.const import DOMAIN
from hvclient import AsyncHargassnerClient
from tests.library_support import Device, schedule_catalog


@pytest.fixture(params=[False, True], ids=["daily", "weekly"])
async def device_server(request):
    device = Device()
    device.catalog.update(schedule_catalog(weekly=request.param))
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", device.handle)
    runner = web.AppRunner(app, shutdown_timeout=0.2)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield device, site._server.sockets[0].getsockname()[1], request.param
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("lost_response", [False, True])
async def test_real_sdk_wire_never_requests_service_or_replays(hass, make_entry, device_server, lost_response):
    device, port, weekly = device_server
    device.channels.update(kesselfuehler=65.5, Hk1Fr35RaumTemp=-1.0)
    name = "3a" if weekly else "3"
    entry = make_entry(writes=("4", "A2a", "A1z", "3", "3a"))
    hass.config_entries.async_update_entry(entry, data={**entry.data, "host": "127.0.0.1", "port": port})
    with patch("custom_components.hargassner_hv.AsyncHargassnerClient", AsyncHargassnerClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    boiler = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_channel_kesselfuehler")
    assert hass.states.get(boiler).state == "65.5"
    assert hass.states.get(boiler).attributes["unit_of_measurement"] == "°C"
    assert registry.async_get(boiler).entity_category is None
    room = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_channel_Hk1Fr35RaumTemp")
    assert registry.async_get(room).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(room) is None

    def entity(platform, name):
        return registry.async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_parameter_{name}")

    assert not device.posts()
    device.disconnect_after_write = lost_response
    if lost_response:
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("number", "set_value", {
                "entity_id": entity("number", "4"), "value": 21,
            }, blocking=True)
        assert len(device.posts()) == 1
    else:
        await hass.services.async_call("number", "set_value", {
            "entity_id": entity("number", "4"), "value": 21,
        }, blocking=True)
        await hass.services.async_call("number", "set_value", {
            "entity_id": entity("number", "A2a"), "value": 0.7,
        }, blocking=True)
        for _ in range(2):
            await hass.services.async_call("select", "select_option", {
                "entity_id": entity("select", "A1z"), "option": "automatic",
            }, blocking=True)
        expected = copy.deepcopy(device.catalog[name]["value"])
        firmware = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_firmware")
        target = registry.async_get(firmware).device_id
        windows = [{"start": "07:00", "end": "09:00"}]
        requested = ({"groups": [{"weekdays": list(range(1, 8)), "time_ranges": windows}]}
                     if weekly else {"time_ranges": windows})
        inactive = ({"time_ranges": windows} if weekly else
                    {"groups": [{"weekdays": list(range(1, 8)), "time_ranges": windows}]})
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(DOMAIN, "set_circuit_program", {
                "device_id": target, "circuit": "1", "kind": "daily" if weekly else "weekly",
                "program": inactive,
            }, blocking=True)
        assert len(device.posts()) == 3
        await hass.services.async_call(DOMAIN, "set_circuit_program", {
            "device_id": target, "circuit": "1", "kind": "weekly" if weekly else "daily", "preview_only": False,
            "program": requested, "expected": {"groups" if weekly else "time_ranges": expected},
        }, blocking=True)
        bodies = [request["body"] for request in device.posts()]
        assert bodies[:3] == [
            {"parameters": [{"name": "4", "value": 21.0}]},
            {"parameters": [{"name": "A2a", "value": 0.7}]},
            {"parameters": [{"name": "A1z", "value": {"id": 1}}]},
        ]
        assert len(bodies) == 4
        value = ([{"weekdays": list(range(1, 8)), "time_ranges": [{"start": "07:00:00", "end": "09:00:00"}]}]
                 if weekly else [{"start": "07:00:00", "end": "09:00:00"}, {"start": "00:00:00", "end": "00:00:00"}])
        assert bodies[-1] == {"parameters": [{"name": name, "value": value}]}
        assert device.catalog["D9"]["value"] is weekly
        assert device.catalog["A1"]["value"]["id"] == 2
    assert all(request["body"]["daqOnly"] is True for request in device.auths())
    assert {request["path"] for request in device.requests} <= {
        "/authenticate", "/challenge", "/info", "/channels", "/parameters",
    }
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not async_get_clientsession(hass).closed
