# SPDX-License-Identifier: Apache-2.0
"""HA -> independently installed SDK -> synthetic loopback HTTP device."""

import copy
from unittest.mock import patch

import pytest
from aiohttp import web
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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


@pytest.mark.parametrize("zone", ["1", "2", "A"])
async def test_climate_real_sdk_persistent_targets_and_runtime_expiry(hass, make_entry, device_server, zone):
    device, port, weekly = device_server
    mode_name, comfort, reduced, selector = {
        "1": ("A1z", "4", "5", "A1"),
        "2": ("A11z", "7", "8", "A11"),
        "A": ("A61z", "HP4", "HP5", "A61"),
    }[zone]
    if zone == "A":
        device.catalog["HP3a" if weekly else "HP3"]["active"] = True
        device.catalog[selector]["value"]["id"] = 2
        device.catalog[comfort]["active"] = device.catalog[reduced]["active"] = True
    device.channels.update({f"Hk{zone}Status": device.catalog[mode_name]["value"]["id"],
                            f"Hk{zone}Fr35RaumTemp": 0})

    def update_runtime(device):
        if device.posts()[-1]["body"]["parameters"][0]["name"] == mode_name:
            device.channels[f"Hk{zone}Status"] = device.catalog[mode_name]["value"]["id"]

    device.after_write = update_runtime
    entry = make_entry(writes=(mode_name, comfort, reduced))
    hass.config_entries.async_update_entry(entry, data={**entry.data, "host": "127.0.0.1", "port": port})
    with patch("custom_components.hargassner_hv.AsyncHargassnerClient", AsyncHargassnerClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, f"{entry.unique_id}_climate_{zone}")

    async def call(service, **data):
        await hass.services.async_call("climate", service, {"entity_id": entity_id, **data}, blocking=True)

    assert not device.posts()
    assert hass.states.get(entity_id).attributes["current_temperature"] == 0
    for preset, mode, target in (("once_comfort", 4, comfort), ("once_reduced", 5, reduced)):
        await call("set_preset_mode", preset_mode=preset)
        before_posts = len(device.posts())
        await call("set_preset_mode", preset_mode=preset)
        assert len(device.posts()) == before_posts  # No one-time rearming.
        with pytest.raises(ServiceValidationError):
            await call("set_temperature", temperature=22, hvac_mode="auto")
        assert len(device.posts()) == before_posts
        await call("set_temperature", temperature=22, hvac_mode="heat")
        assert device.posts()[-1]["body"] == {"parameters": [{"name": target, "value": 22.0}]}
        device.channels[f"Hk{zone}Status"] = 1  # Expiry can leave hidden command parameter unchanged.
        await entry.runtime_data.channels.async_refresh()
        await entry.runtime_data.parameters.async_refresh()
        assert hass.states.get(entity_id).state == "auto"
        assert device.catalog[mode_name]["value"]["id"] == mode
        assert device.catalog[target]["value"] == 22
        assert len(device.posts()) == before_posts + 1
        with pytest.raises(ServiceValidationError):
            await call("set_preset_mode", preset_mode=preset)
        assert len(device.posts()) == before_posts + 1  # No forced/replayed old stored command.
    assert len(device.posts()) == 4
    assert all(request["body"]["daqOnly"] is True for request in device.auths())
    assert {request["path"] for request in device.requests} <= {
        "/authenticate", "/challenge", "/info", "/channels", "/parameters",
    }
    assert device.catalog["D9"]["value"] is weekly
    assert device.catalog[selector]["value"]["id"] == 2
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(device.posts()) == 4


@pytest.mark.parametrize("service,data", [
    ("set_preset_mode", {"preset_mode": "once_comfort"}),
    ("set_temperature", {"temperature": 22}),
])
async def test_climate_lost_response_does_not_retry(hass, make_entry, device_server, service, data):
    device, port, _ = device_server
    device.channels["Hk1Status"] = 3
    entry = make_entry(writes=("A1z", "4"))
    hass.config_entries.async_update_entry(entry, data={**entry.data, "host": "127.0.0.1", "port": port})
    with patch("custom_components.hargassner_hv.AsyncHargassnerClient", AsyncHargassnerClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, f"{entry.unique_id}_climate_1")
    device.disconnect_after_write = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("climate", service, {"entity_id": entity_id, **data}, blocking=True)
    assert len(device.posts()) == 1
    assert all(request["body"]["daqOnly"] is True for request in device.auths())
