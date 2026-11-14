# SPDX-License-Identifier: Apache-2.0
"""Guarded controls, program actions, lifecycle and recovery on real HA core."""

import asyncio
import copy
import json

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.hargassner_hv.const import (
    CONF_ACK_WRITES,
    CONF_ENABLED_WRITES,
    CONF_PCB,
    CONF_RAW_CHANNELS,
    DOMAIN,
)
from custom_components.hargassner_hv.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hargassner_hv.number import HargassnerNumber
from custom_components.hargassner_hv.sensor import RawChannelSensor
from hvclient import (
    DailySchedule,
    ProtocolError,
    TimeRange,
    TransportError,
    ValidationError,
    WriteMismatchError,
    WriteOutcomeUnknownError,
    WritePreview,
    WriteResult,
)
from hvclient.models import system_info_from_json


def entity(hass, entry, platform, name):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_parameter_{name}")


def device(hass, entry):
    registry = er.async_get(hass)
    firmware = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_firmware")
    return registry.async_get(firmware).device_id


async def load(hass, make_entry, **kwargs):
    entry = make_entry(**kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize("options", [
    {CONF_ENABLED_WRITES: ["4"], CONF_ACK_WRITES: False},
    {CONF_ENABLED_WRITES: ["D9"], CONF_ACK_WRITES: True},
    {CONF_ENABLED_WRITES: "4", CONF_ACK_WRITES: True},
    {CONF_ENABLED_WRITES: [], CONF_RAW_CHANNELS: "false"},
])
async def test_corrupt_options_cannot_enable_writes(hass, client, make_entry, options):
    entry = make_entry(options=options)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    client.async_get_info.assert_not_awaited()
    client.async_set_parameter.assert_not_awaited()


async def test_program_preview_returns_limits_without_writing(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("3",))
    parameter = entry.runtime_data.parameter("3")
    requested = DailySchedule((TimeRange("07:00", "09:00"),))
    client.async_preview_parameter_write.return_value = WritePreview(parameter, requested, 2)
    response = await hass.services.async_call(DOMAIN, "set_circuit_program", {
        "device_id": device(hass, entry), "circuit": "1", "kind": "daily",
        "program": {"time_ranges": [{"start": "07:00", "end": "09:00"}]},
    }, blocking=True, return_response=True)
    assert response["outcome"] == "preview"
    assert response["limits"]["scope"] == "reviewed_sdk_write_policy"
    assert response["current"] == parameter.to_dict()["value"]
    client.async_set_parameter.assert_not_awaited()
    assert json.dumps(response)


async def test_program_apply_requires_expected_and_does_one_sdk_write(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("3",))
    data = {"device_id": device(hass, entry), "circuit": "1", "kind": "daily", "preview_only": False,
            "program": {"time_ranges": [{"start": "07:00", "end": "09:00"}]}}
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_circuit_program", data, blocking=True)
    client.async_set_parameter.assert_not_awaited()
    expected = copy.deepcopy(client.catalog["3"]["value"])
    response = await hass.services.async_call(DOMAIN, "set_circuit_program", {
        **data, "expected": {"time_ranges": expected},
    }, blocking=True, return_response=True)
    assert response["outcome"] == "verified"
    assert client.async_set_parameter.await_count == 1
    assert client.async_set_parameter.await_args.args[0] == "3"
    assert client.async_set_parameter.await_args.kwargs == {"expected": expected}


@pytest.mark.parametrize("program", [
    {"time_ranges": [{"start": "22:00", "end": "06:00"}]},
    {"time_ranges": [], "parameter": "D9"},
    {"groups": []},
])
async def test_invalid_program_is_rejected_before_io(hass, client, make_entry, program):
    entry = await load(hass, make_entry, writes=("3",))
    calls = client.async_get_info.await_count
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_circuit_program", {
            "device_id": device(hass, entry), "circuit": "1", "kind": "daily", "program": program,
        }, blocking=True)
    assert client.async_get_info.await_count == calls
    client.async_set_parameter.assert_not_awaited()


async def test_program_requires_opt_in_even_for_preview(hass, client, make_entry):
    entry = await load(hass, make_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_circuit_program", {
            "device_id": device(hass, entry), "circuit": "1", "kind": "daily", "program": {"time_ranges": []},
        }, blocking=True)
    client.async_preview_parameter_write.assert_not_awaited()
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("exception", [WriteOutcomeUnknownError, WriteMismatchError])
async def test_uncertain_writes_never_retry_or_claim_requested_state(hass, client, make_entry, exception):
    entry = await load(hass, make_entry, writes=("4",))
    before = entry.runtime_data.parameter("4")
    result = WriteResult("4", 21, before, before, before, "unknown")
    client.async_set_parameter.side_effect = exception(result)
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.async_control("4", 21)
    assert client.async_set_parameter.await_count == 1
    assert entry.runtime_data.parameter("4").value == 20.5


async def test_stale_value_refusal_is_not_replayed(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("4",))
    client.async_set_parameter.side_effect = ValidationError("synthetic-private-details")
    with pytest.raises(ServiceValidationError) as caught:
        await entry.runtime_data.async_control("4", 21)
    assert "synthetic-private-details" not in str(caught.value)
    assert client.async_set_parameter.await_count == 1


async def test_identity_change_prevents_control(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("4",))
    client.async_get_info.return_value = system_info_from_json({"mac": "02:00:00:00:00:02"})
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_control("4", 21)
    client.async_set_parameter.assert_not_awaited()
    assert not entry.runtime_data.parameters.last_update_success


async def test_poll_failure_unavailable_then_recovery(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("4",))
    coordinator = entry.runtime_data.parameters
    original = client.async_get_parameters.side_effect
    client.async_get_parameters.side_effect = TransportError("synthetic-private-details")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert hass.states.get(entity(hass, entry, "number", "4")).state == "unavailable"
    client.async_get_parameters.side_effect = original
    await coordinator.async_refresh()
    assert hass.states.get(entity(hass, entry, "number", "4")).state == "20.5"
    client.async_set_parameter.assert_not_awaited()


async def test_unverified_sdk_return_is_not_treated_as_success(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("4",))
    parameter = entry.runtime_data.parameter("4")
    client.async_set_parameter.side_effect = None
    client.async_set_parameter.return_value = WriteResult("4", 21, parameter, parameter, None, "unknown")
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.async_control("4", 21)
    assert client.async_set_parameter.await_count == 1
    assert entry.runtime_data.parameter("4").value == 20.5


async def test_setup_failure_closes_client_without_closing_shared_session(hass, client, make_entry):
    session = async_get_clientsession(hass)
    entry = make_entry()
    client.async_get_channels.side_effect = ProtocolError("synthetic-private-details")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    client.async_close.assert_awaited_once()
    assert not session.closed


async def test_unload_cancels_active_control_without_replay(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("4",))
    runtime = entry.runtime_data
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    client.async_set_parameter.side_effect = blocked
    task = asyncio.create_task(runtime.async_control("4", 21))
    await entered.wait()
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.closed
    assert not runtime._operations
    assert client.async_set_parameter.await_count == 1
    assert not async_get_clientsession(hass).closed


async def test_optional_diagnostics_and_raw_channels_are_not_assigned_guessed_units(hass, client, make_entry):
    entry = await load(hass, make_entry, options={CONF_RAW_CHANNELS: True, CONF_PCB: True})
    sensor = RawChannelSensor(entry, "synthetic.raw")
    assert sensor.native_value == 0
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert not sensor.entity_registry_enabled_default
    diagnostic_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_channel_synthetic.raw")
    assert er.async_get(hass).async_get(diagnostic_id).disabled_by is not None


async def test_diagnostics_are_a_strict_whitelist(hass, client, make_entry):
    entry = await load(hass, make_entry)
    data = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(data)
    for secret in ("boiler.example", "02:00:00:00:00:01", "synthetic-system", "synthetic-secret",
                   "synthetic-commission", "synthetic-serial", entry.unique_id, "06:00", "20.5"):
        assert secret not in serialized
    assert data["safety_policy"] == {"service_mode_initiating_calls": "excluded"}
    assert "daq_only" not in serialized


async def test_integer_number_does_not_send_float_to_integer_sdk_parameter(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("2",))
    number = HargassnerNumber(entry, "2", "Synthetic DHW target")
    await number.async_set_native_value(11.0)
    assert type(client.async_set_parameter.await_args.args[1]) is int
    with pytest.raises(ServiceValidationError):
        await number.async_set_native_value(11.5)
    assert client.async_set_parameter.await_count == 1


async def test_unloaded_device_action_is_rejected_without_sdk_call(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("3",))
    target = device(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_circuit_program", {
            "device_id": target, "circuit": "1", "kind": "daily", "program": {"time_ranges": []},
        }, blocking=True)
    client.async_preview_parameter_write.assert_not_awaited()
    client.async_set_parameter.assert_not_awaited()


async def test_program_action_is_admin_only(hass, client, make_entry):
    from homeassistant.auth import auth_manager_from_config
    from homeassistant.auth.const import GROUP_ID_USER
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    hass.auth = await auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    await hass.auth.async_create_user("Synthetic owner")
    user = await hass.auth.async_create_user("Synthetic user", group_ids=[GROUP_ID_USER])
    assert not user.is_admin
    entry = await load(hass, make_entry, writes=("3",))
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "set_circuit_program", {
            "device_id": device(hass, entry), "circuit": "1", "kind": "daily", "program": {"time_ranges": []},
        }, context=Context(user_id=user.id), blocking=True)
    client.async_preview_parameter_write.assert_not_awaited()
    client.async_set_parameter.assert_not_awaited()
