# SPDX-License-Identifier: Apache-2.0
"""Real-HA climate commands, capability gating, expiry and observation ordering."""

import asyncio
from dataclasses import replace
from time import monotonic

import pytest
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.hargassner_hv.climate import PRESETS, TARGETS, HargassnerClimate
from custom_components.hargassner_hv.const import DOMAIN
from hvclient import Choice, TransportError, WriteOutcomeUnknownError, WriteResult
from hvclient.models import channels_from_json

MODE_NAMES = {"1": "A1z", "2": "A11z", "A": "A61z"}


def climate_id(hass, entry, zone="1"):
    return er.async_get(hass).async_get_entity_id("climate", DOMAIN, f"{entry.unique_id}_climate_{zone}")


async def load(hass, client, make_entry, *, writes=("A1z", "4", "5"), mode=3, zone="1"):
    client.catalog[MODE_NAMES[zone]]["value"]["id"] = mode
    if zone == "A":
        client.catalog["HP3"]["active"] = True
        client.catalog["HP4"]["active"] = client.catalog["HP5"]["active"] = True

    async def channels():
        return channels_from_json({
            **dict(client.async_get_channels.return_value),
            **{f"Hk{key}Status": client.catalog[name]["value"]["id"] for key, name in MODE_NAMES.items()},
        })

    client.async_get_channels.side_effect = channels
    entry = make_entry(writes=writes)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def call(hass, entry, service, zone="1", **data):
    await hass.services.async_call("climate", service, {
        "entity_id": climate_id(hass, entry, zone), **data,
    }, blocking=True)


@pytest.mark.parametrize("zone", ["1", "2", "A"])
@pytest.mark.parametrize("mode", range(6))
async def test_six_modes_and_target_routing(hass, client, make_entry, zone, mode):
    entry = await load(hass, client, make_entry, zone=zone, mode=mode,
                       writes=(MODE_NAMES[zone], *TARGETS[zone]))
    entity_id = climate_id(hass, entry, zone)
    state = hass.states.get(entity_id)
    assert state.state == ("off" if mode == 0 else "auto" if mode == 1 else "heat")
    assert state.attributes["preset_mode"] == ("none" if mode < 2 else list(PRESETS)[mode - 2])
    assert "hvac_action" not in state.attributes
    assert er.async_get(hass).async_get(entity_id).entity_category is None
    assert set(state.attributes["hvac_modes"]) == {"off", "auto", "heat"}
    if mode < 2:
        assert "temperature" not in state.attributes
        assert not state.attributes["supported_features"] & ClimateEntityFeature.TARGET_TEMPERATURE
    else:
        assert state.attributes["temperature"] == 20.5
        assert state.attributes["min_temp"] == 0
        assert state.attributes["max_temp"] == 30
        assert state.attributes["target_temp_step"] == 0.5
        await call(hass, entry, "set_temperature", zone, temperature=21, hvac_mode="heat")
        name = TARGETS[zone][0 if mode in (3, 4) else 1]
        client.async_set_parameter.assert_awaited_once_with(name, 21.0, expected=20.5)
        assert hass.states.get(entity_id).attributes["temperature"] == 21


@pytest.mark.parametrize(("initial", "service", "data", "requested"), [
    (3, "set_hvac_mode", {"hvac_mode": "off"}, 0),
    (4, "set_hvac_mode", {"hvac_mode": "auto"}, 1),
    (0, "set_hvac_mode", {"hvac_mode": "heat"}, 3),
    (1, "set_hvac_mode", {"hvac_mode": "heat"}, 3),
    *[(mode, "set_hvac_mode", {"hvac_mode": "heat"}, None) for mode in range(2, 6)],
    *[(mode, "set_preset_mode", {"preset_mode": "none"}, 1 if mode >= 2 else None)
      for mode in range(6)],
    *[(0, "set_preset_mode", {"preset_mode": preset}, mode.value) for preset, mode in PRESETS.items()],
    *[(mode.value, "set_preset_mode", {"preset_mode": preset}, None) for preset, mode in PRESETS.items()],
    (0, "turn_on", {}, 1), (4, "turn_on", {}, 1), (1, "turn_on", {}, None),
    (3, "turn_off", {}, 0), (0, "turn_off", {}, None),
])
async def test_command_matrix(hass, client, make_entry, initial, service, data, requested):
    entry = await load(hass, client, make_entry, mode=initial)
    await call(hass, entry, service, **data)
    if requested is None:
        client.async_set_parameter.assert_not_awaited()
    else:
        client.async_set_parameter.assert_awaited_once_with("A1z", requested, expected=initial)
        state = hass.states.get(climate_id(hass, entry))
        assert state.state == ("off" if requested == 0 else "auto" if requested == 1 else "heat")


async def test_registration_is_per_circuit_and_default_deny(hass, client, make_entry):
    entry = await load(hass, client, make_entry, writes=())
    assert all(climate_id(hass, entry, zone) is None for zone in MODE_NAMES)
    client.async_set_parameter.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    entry = await load(hass, client, make_entry, writes=tuple(MODE_NAMES.values()))
    assert climate_id(hass, entry, "1") is not None
    assert climate_id(hass, entry, "2") is not None
    assert climate_id(hass, entry, "A") is None
    client.catalog["HP3"]["active"] = True
    await entry.runtime_data.parameters.async_refresh()
    await hass.async_block_till_done()
    assert climate_id(hass, entry, "A") is not None
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("mode", [2, 3, 4, 5])
async def test_partial_target_permission(hass, client, make_entry, mode):
    entry = await load(hass, client, make_entry, mode=mode, writes=("A1z", "4"))
    state = hass.states.get(climate_id(hass, entry))
    assert bool(state.attributes["supported_features"] & ClimateEntityFeature.TARGET_TEMPERATURE) == (mode in (3, 4))
    if mode in (2, 5):
        with pytest.raises(ServiceValidationError):
            await call(hass, entry, "set_temperature", temperature=21)
        client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("data", [
    {"temperature": 21, "hvac_mode": "auto"},
    {"temperature": 21, "hvac_mode": "off"},
    {"target_temp_low": 19, "target_temp_high": 21},
    {"temperature": 21.25}, {"temperature": float("nan")},
    {"temperature": 21.00000001},
    {"temperature": float("inf")}, {"temperature": -0.5}, {"temperature": 30.5},
])
async def test_invalid_temperature_actions_do_not_write(hass, client, make_entry, data):
    entry = await load(hass, client, make_entry)
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "set_temperature", **data)
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("mode", [0, 1])
async def test_combined_heat_and_target_cannot_enable_circuit(hass, client, make_entry, mode):
    entry = await load(hass, client, make_entry, mode=mode)
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "set_temperature", temperature=21, hvac_mode="heat")
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("value", [-1, 0, 21.25, None, True, "21"])
async def test_room_feedback_is_optional_and_never_flow_temperature(hass, client, make_entry, value):
    entry = await load(hass, client, make_entry)
    data = dict(entry.runtime_data.channels.data)
    data.update(Hk1Soll=55, HkmHk1Fuehler=50)
    if value is None:
        data.pop("Hk1Fr35RaumTemp", None)
    else:
        data["Hk1Fr35RaumTemp"] = value
    entry.runtime_data.channels.async_set_updated_data(data)
    climate = HargassnerClimate(entry, "1")
    assert climate.available
    assert climate.native_current_temperature == (value if type(value) in (int, float) and value != -1 else None)


@pytest.mark.parametrize("mode", [4, 5])
async def test_runtime_expiry_wins_over_stale_parameter_and_keeps_target(hass, client, make_entry, mode):
    entry = await load(hass, client, make_entry, mode=mode)
    await call(hass, entry, "set_temperature", temperature=22)
    runtime = entry.runtime_data
    assert runtime.parameter("A1z").value.id == mode
    data = dict(runtime.channels.data)
    data["Hk1Status"] = 1  # Firmware expiry, without changing last command parameter.
    client.async_get_channels.side_effect = None
    client.async_get_channels.return_value = channels_from_json(data)
    await runtime.channels.async_refresh()
    await runtime.parameters.async_refresh()
    state = hass.states.get(climate_id(hass, entry))
    assert state.state == "auto" and state.attributes["preset_mode"] == "none"
    assert "temperature" not in state.attributes
    assert runtime.parameter("4" if mode == 4 else "5").value == 22
    assert client.async_set_parameter.await_count == 1


async def test_old_channel_poll_cannot_overwrite_verified_mode(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    runtime = entry.runtime_data
    await call(hass, entry, "set_preset_mode", preset_mode="once_reduced")
    climate = HargassnerClimate(entry, "1")
    assert climate.mode == 5  # Last channels still say 3, verified mode readback says 5.
    assert runtime.channels.data["Hk1Status"] == 3
    runtime.channels.async_set_updated_data(dict(runtime.channels.data))
    assert climate.mode == 5
    runtime.channels.observed_at = monotonic()
    runtime.channels.async_set_updated_data({**dict(runtime.channels.data), "Hk1Status": 1})
    assert climate.mode == 1
    await runtime.parameters.async_refresh()
    assert climate.mode == 1
    client.async_set_parameter.assert_awaited_once()


@pytest.mark.parametrize("value", [None, -1, 6, 2.5, True, "3"])
async def test_invalid_or_missing_mode_never_falls_back_to_parameter(hass, client, make_entry, value):
    entry = await load(hass, client, make_entry)
    data = dict(entry.runtime_data.channels.data)
    if value is None:
        data.pop("Hk1Status")
    else:
        data["Hk1Status"] = value
    entry.runtime_data.channels.async_set_updated_data(data)
    assert hass.states.get(climate_id(hass, entry)).state == "unavailable"
    with pytest.raises(ServiceValidationError):
        await HargassnerClimate(entry, "1").async_turn_on()
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("coordinator", ["channels", "parameters"])
async def test_poll_failure_recovery(hass, client, make_entry, coordinator):
    entry = await load(hass, client, make_entry)
    method = getattr(client, f"async_get_{coordinator}")
    original = method.side_effect
    method.side_effect = TransportError("synthetic-private-detail")
    await getattr(entry.runtime_data, coordinator).async_refresh()
    assert hass.states.get(climate_id(hass, entry)).state == "unavailable"
    method.side_effect = original
    await getattr(entry.runtime_data, coordinator).async_refresh()
    assert hass.states.get(climate_id(hass, entry)).state == "heat"
    client.async_set_parameter.assert_not_awaited()


async def test_preflight_expiry_refuses_target_without_post(hass, client, make_entry):
    entry = await load(hass, client, make_entry, mode=4)
    client.async_get_channels.side_effect = None
    client.async_get_channels.return_value = channels_from_json({"Hk1Status": 1})
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "set_temperature", temperature=21)
    client.async_set_parameter.assert_not_awaited()


async def test_stored_command_matching_request_cannot_fake_runtime_transition(hass, client, make_entry):
    entry = await load(hass, client, make_entry, mode=4)
    runtime = entry.runtime_data
    runtime.channels.observed_at = monotonic()
    runtime.channels.async_set_updated_data({**dict(runtime.channels.data), "Hk1Status": 1})
    with pytest.raises(ServiceValidationError):
        await HargassnerClimate(entry, "1").async_set_preset_mode("once_comfort")
    assert hass.states.get(climate_id(hass, entry)).state == "auto"
    client.async_set_parameter.assert_not_awaited()


async def test_unchanged_sdk_read_is_not_a_new_runtime_mode_write(hass, client, make_entry):
    entry = await load(hass, client, make_entry, mode=4)
    runtime = entry.runtime_data
    runtime.channels.observed_at = monotonic()
    runtime.channels.async_set_updated_data({**dict(runtime.channels.data), "Hk1Status": 1})
    parameter = runtime.parameter("A1z")
    client.async_set_parameter.side_effect = None
    client.async_set_parameter.return_value = WriteResult("A1z", 4, parameter, parameter, None, "unchanged")
    await runtime.async_control("A1z", 4)
    assert "A1z" not in runtime.mode_readbacks
    assert hass.states.get(climate_id(hass, entry)).state == "auto"


async def test_queued_target_is_not_rerouted_after_local_mode_write(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    runtime = entry.runtime_data
    climate = HargassnerClimate(entry, "1")
    async with runtime._control_lock:
        task = asyncio.create_task(climate.async_set_temperature(temperature=21))
        # Let the operation reach the lock, using an event-loop barrier.
        barrier = asyncio.Event()
        hass.loop.call_soon(barrier.set)
        await barrier.wait()
        runtime.mode_readbacks["A1z"] = (2, monotonic())
    with pytest.raises(ServiceValidationError):
        await task
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("change", [
    {"type": "INTEGER"}, {"unit": ""}, {"active": False},
    {"security_level": "PLUMBER"}, {"increment": 0}, {"minimum": 40},
])
async def test_changed_target_contract_removes_dial(hass, client, make_entry, change):
    entry = await load(hass, client, make_entry)
    runtime = entry.runtime_data
    data = runtime.parameters.data
    runtime.parameters.async_set_updated_data(replace(data, parameters={
        **data.parameters, "4": replace(runtime.parameter("4"), **change),
    }))
    climate = HargassnerClimate(entry, "1")
    assert climate.available and climate.target_parameter is None
    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(temperature=21)
    client.async_set_parameter.assert_not_awaited()


async def test_changed_mode_contract_disables_control(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    runtime = entry.runtime_data
    data = runtime.parameters.data
    runtime.parameters.async_set_updated_data(replace(data, parameters={
        **data.parameters, "A1z": replace(runtime.parameter("A1z"), value=Choice(6, "Unknown")),
    }))
    assert not HargassnerClimate(entry, "1").available
    client.async_set_parameter.assert_not_awaited()


async def test_service_device_targeting_and_unrecognized_fields(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    record = er.async_get(hass).async_get(climate_id(hass, entry))
    await hass.services.async_call("climate", "set_temperature", {
        "device_id": record.device_id, "temperature": 21,
    }, blocking=True)
    client.async_set_parameter.assert_awaited_once_with("4", 21.0, expected=20.5)
    with pytest.raises(ServiceValidationError):
        await HargassnerClimate(entry, "1").async_set_temperature(temperature=22, arbitrary_parameter="D9")
    client.async_set_parameter.assert_awaited_once()


async def test_ha_converts_fahrenheit_to_native_celsius(hass, client, make_entry):
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = await load(hass, client, make_entry)
    await call(hass, entry, "set_temperature", temperature=69.8, hvac_mode="heat")
    client.async_set_parameter.assert_awaited_once_with("4", 21.0, expected=20.5)
    assert hass.states.get(climate_id(hass, entry)).attributes["temperature"] == 70


async def test_real_inflight_old_poll_preserves_verified_mode(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    runtime = entry.runtime_data
    entered, release = asyncio.Event(), asyncio.Event()
    old = dict(runtime.channels.data)

    async def blocked_channels():
        entered.set()
        await release.wait()
        return channels_from_json(old)

    client.async_get_channels.side_effect = blocked_channels
    polling = asyncio.create_task(runtime.channels.async_refresh())
    await entered.wait()
    # The existing select uses the same verified control path and lock.
    await runtime.async_control("A1z", 5)
    release.set()
    await polling
    assert hass.states.get(climate_id(hass, entry)).attributes["preset_mode"] == "once_reduced"
    client.async_get_channels.side_effect = None
    client.async_get_channels.return_value = channels_from_json({**old, "Hk1Status": 1})
    await runtime.channels.async_refresh()
    assert hass.states.get(climate_id(hass, entry)).state == "auto"
    client.async_set_parameter.assert_awaited_once()


async def test_unload_cancels_active_and_queued_climate_controls(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    climate = HargassnerClimate(entry, "1")
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    client.async_set_parameter.side_effect = blocked
    active = asyncio.create_task(climate.async_set_temperature(temperature=21))
    await entered.wait()
    queued = asyncio.create_task(climate.async_set_temperature(temperature=22))
    barrier = asyncio.Event()
    hass.loop.call_soon(barrier.set)
    await barrier.wait()
    assert await hass.config_entries.async_unload(entry.entry_id)
    for task in (active, queued):
        with pytest.raises(asyncio.CancelledError):
            await task
    client.async_set_parameter.assert_awaited_once()


async def test_uncertain_mode_write_is_not_replayed_or_optimistic(hass, client, make_entry):
    entry = await load(hass, client, make_entry)
    parameter = entry.runtime_data.parameter("A1z")
    client.async_set_parameter.side_effect = WriteOutcomeUnknownError(
        WriteResult("A1z", 4, parameter, parameter, None, "unknown")
    )
    with pytest.raises(HomeAssistantError):
        await call(hass, entry, "set_preset_mode", preset_mode="once_comfort")
    assert hass.states.get(climate_id(hass, entry)).attributes["preset_mode"] == "permanent_comfort"
    client.async_set_parameter.assert_awaited_once()
    climate = HargassnerClimate(entry, "1")
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await climate.async_turn_on()
    client.async_set_parameter.assert_awaited_once()
