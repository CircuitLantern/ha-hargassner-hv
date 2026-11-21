# SPDX-License-Identifier: Apache-2.0
"""Circuit-mode and regulation-state diagnostics do not alter SDK or controls."""

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hargassner_hv.const import CONF_RAW_CHANNELS, DOMAIN
from custom_components.hargassner_hv.hvclient.models import channels_from_json
from custom_components.hargassner_hv.sensor import HeatingCircuitDiagnosticSensor

MODE_CASES = [
    (0, "off"), (1, "automatic"), (2, "continuous reduced-temperature operation"),
    (3, "continuous comfort heating"), (4, "one-time comfort heating"),
    (5, "one-time reduced-temperature operation"), (1.0, "automatic"),
    (6, None), (-1, None), (1.5, None), (None, None),
]
STATE_CASES = [
    (0, "off"), (1, "comfort heating"), (2, "reduced-temperature operation"),
    (3, "frost protection"), (4, "off above daytime-reduction outdoor-temperature limit"),
    (5, "off above nighttime-reduction outdoor-temperature limit"),
    (6, "off above general outdoor-temperature heating limit"),
    (7, "overtemperature / heat dissipation"), (8, "chimney-sweep / emissions-test operation"),
    (9, "residual-heat utilization"), (3.0, "frost protection"),
    (10, None), (-1, None), (3.5, None), (None, None),
]
CHANNELS = [f"Hk{circuit}{suffix}" for circuit in ("1", "2", "A") for suffix in ("Status", "Zustand")]


def entity_id(hass, entry, channel):
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_channel_{channel}")


@pytest.mark.parametrize("circuit", ["1", "2", "A"])
@pytest.mark.parametrize("suffix,value,expected", [
    *(('Status', value, expected) for value, expected in MODE_CASES),
    *(('Zustand', value, expected) for value, expected in STATE_CASES),
])
async def test_circuit_diagnostic_exact_mapping(hass, client, make_entry, circuit, suffix, value, expected):
    channel = f"Hk{circuit}{suffix}"
    client.async_get_channels.return_value = channels_from_json({} if value is None else {channel: value})
    entry = make_entry(options={CONF_RAW_CHANNELS: True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensor = HeatingCircuitDiagnosticSensor(entry, channel)
    assert sensor.available is (expected is not None)
    assert sensor.native_value == expected
    assert sensor.device_class == "enum"
    assert len(sensor.options) == len(set(sensor.options)) == (6 if suffix == "Status" else 10)
    if expected is not None:
        assert expected in sensor.options
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert sensor.extra_state_attributes == {"channel": channel, "raw_value": value}
    if value is None:
        assert entity_id(hass, entry, channel) is None
    else:
        row = er.async_get(hass).async_get(entity_id(hass, entry, channel))
        label = "operating mode" if suffix == "Status" else "regulation state"
        assert row.original_name == f"Heating circuit {circuit} {label}"
        assert row.entity_category is EntityCategory.DIAGNOSTIC
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert entry.runtime_data.channels.data[channel] == value
    assert not entry.runtime_data.enabled_writes
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("disabled", [None, er.RegistryEntryDisabler.INTEGRATION, er.RegistryEntryDisabler.USER])
async def test_circuit_diagnostic_preserves_registry_and_availability(hass, client, make_entry, channel, disabled):
    client.async_get_channels.return_value = channels_from_json({channel: 0})
    entry = make_entry(options={CONF_RAW_CHANNELS: True})
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_channel_{channel}", config_entry=entry,
        original_name=f"Raw {channel}", entity_category=EntityCategory.DIAGNOSTIC, disabled_by=disabled,
    )
    registry.async_update_entity(old.entity_id, name="My circuit diagnostic")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    row = registry.async_get(old.entity_id)
    assert entity_id(hass, entry, channel) == old.entity_id
    assert row.unique_id == old.unique_id
    assert row.name == "My circuit diagnostic"
    assert row.disabled_by is disabled
    assert row.entity_category is EntityCategory.DIAGNOSTIC
    assert sum(item.unique_id == row.unique_id for item in
               er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    if disabled is None:
        cases = MODE_CASES if channel.endswith("Status") else STATE_CASES
        for value, expected in cases + [(0, "off")]:
            client.async_get_channels.return_value = channels_from_json({} if value is None else {channel: value})
            await entry.runtime_data.channels.async_refresh()
            state = hass.states.get(old.entity_id)
            assert state.state == (expected if expected is not None else "unavailable")
            assert state.attributes["device_class"] == "enum"
            if expected is not None:
                assert state.attributes["raw_value"] == value
                assert state.attributes["channel"] == channel
                assert expected in state.attributes["options"]
        entry.runtime_data.channels.async_set_update_error(UpdateFailed("Synthetic poll failure"))
        assert hass.states.get(old.entity_id).state == "unavailable"
    else:
        assert hass.states.get(old.entity_id) is None
    client.async_set_parameter.assert_not_awaited()


async def test_circuit_diagnostics_are_optional_and_discovered_later(hass, client, make_entry):
    values = dict.fromkeys(CHANNELS, 0)
    client.async_get_channels.return_value = channels_from_json(values)
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert all(entity_id(hass, entry, channel) is None for channel in CHANNELS)
    await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={CONF_RAW_CHANNELS: True})
    client.async_get_channels.return_value = channels_from_json({})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client.async_get_channels.return_value = channels_from_json(values)
    await entry.runtime_data.channels.async_refresh()
    await hass.async_block_till_done()
    for channel in CHANNELS:
        row = er.async_get(hass).async_get(entity_id(hass, entry, channel))
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert row.original_name != f"Raw {channel}"
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("circuit", ["1", "2", "A"])
async def test_circuit_operating_mode_and_regulation_state_are_independent(hass, client, make_entry, circuit):
    status_channel, state_channel = f"Hk{circuit}Status", f"Hk{circuit}Zustand"
    client.async_get_channels.return_value = channels_from_json({status_channel: 1, state_channel: 2})
    entry = make_entry(options={CONF_RAW_CHANNELS: True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mode = HeatingCircuitDiagnosticSensor(entry, status_channel)
    regulation = HeatingCircuitDiagnosticSensor(entry, state_channel)
    assert mode.native_value == "automatic"
    assert regulation.native_value == "reduced-temperature operation"
    for status, state, expected_mode, expected_state in [
        (1, 6, "automatic", "off above general outdoor-temperature heating limit"),
        (0, 3, "off", "frost protection"),
        (6, 3, None, "frost protection"),
        (1, 10, "automatic", None),
    ]:
        client.async_get_channels.return_value = channels_from_json({status_channel: status, state_channel: state})
        await entry.runtime_data.channels.async_refresh()
        assert mode.native_value == expected_mode
        assert regulation.native_value == expected_state
        assert mode.available is (expected_mode is not None)
        assert regulation.available is (expected_state is not None)
        assert entry.runtime_data.channels.data[status_channel] == status
        assert entry.runtime_data.channels.data[state_channel] == state
    client.async_set_parameter.assert_not_awaited()


async def test_circuit_diagnostics_do_not_guess_unknown_names(hass, client, make_entry):
    values = dict.fromkeys(["Hk3Status", "Hk3Zustand", "Hk1status", "Boiler1Status", "Boiler1Zustand"], 3)
    client.async_get_channels.return_value = channels_from_json(values)
    entry = make_entry(options={CONF_RAW_CHANNELS: True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for channel in values:
        row = er.async_get(hass).async_get(entity_id(hass, entry, channel))
        assert row.original_name == f"Raw {channel}"
    client.async_set_parameter.assert_not_awaited()
