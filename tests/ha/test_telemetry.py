# SPDX-License-Identifier: Apache-2.0
"""Operational readings are primary sensors, not setting readbacks or debug data."""

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hargassner_hv.const import CONF_RAW_CHANNELS, DOMAIN
from custom_components.hargassner_hv.hvclient.models import channels_from_json
from custom_components.hargassner_hv.sensor import (
    BoilerStateSensor,
    DoorContactSensor,
    OperationalChannelSensor,
)
from custom_components.hargassner_hv.telemetry import OPERATIONAL_CHANNELS


def channel_id(hass, entry, name):
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_channel_{name}")


async def load(hass, make_entry, **kwargs):
    entry = make_entry(**kwargs)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize("name,value,unit,state_class", [
    ("kesselfuehler", 0.0, "°C", "measurement"),
    ("aussentemperatur", -3.2, "°C", "measurement"),
    ("puffer_oben", 65.0, "°C", "measurement"),
    ("HkmBoilerFuehler", 47.5, "°C", "measurement"),
    ("HkmHk1Fuehler", 34.0, "°C", "measurement"),
    ("Hk1Fr35RaumTemp", 21.25, "°C", None),
    ("Hk1Soll", 38.5, "°C", None),
    ("lambda_O2", 6.2, "%", "measurement"),
    ("Pufferfuellgrad", 70.0, "%", None),
    ("Leistung", 82.0, "%", None),
    ("saugzug_rpm", 0, "rpm", "measurement"),
    ("SzSollRPM", 1250, "rpm", None),
])
async def test_reviewed_readings_are_enabled_primary_sensors_without_raw_option(
    hass, client, make_entry, name, value, unit, state_class,
):
    client.async_get_channels.return_value = channels_from_json({name: value})
    entry = await load(hass, make_entry)
    row = er.async_get(hass).async_get(channel_id(hass, entry, name))
    state = hass.states.get(row.entity_id)
    assert row.entity_category is None
    assert row.disabled_by is None
    assert float(state.state) == value
    assert state.attributes["unit_of_measurement"] == unit
    assert state.attributes.get("state_class") == state_class
    assert "Raw " not in row.original_name
    client.async_set_parameter.assert_not_awaited()


async def test_room_sentinel_is_unavailable_not_a_fabricated_measurement(hass, client, make_entry):
    client.async_get_channels.return_value = channels_from_json({"Hk1Fr35RaumTemp": -1.0})
    entry = await load(hass, make_entry)
    sensor = OperationalChannelSensor(entry, OPERATIONAL_CHANNELS["Hk1Fr35RaumTemp"])
    assert not sensor.available
    assert sensor.native_value is None
    assert not sensor.entity_registry_enabled_default
    assert sensor.state_class is None
    client.async_get_channels.return_value = channels_from_json({"Hk1Fr35RaumTemp": 0.0})
    await entry.runtime_data.channels.async_refresh()
    assert sensor.available
    assert sensor.native_value == 0.0  # Zero is not treated as missing.


async def test_missing_channel_goes_unavailable_without_stale_or_zero_values(hass, client, make_entry):
    entry = await load(hass, make_entry)
    entity_id = channel_id(hass, entry, "kesselfuehler")
    client.async_get_channels.return_value = channels_from_json({})
    await entry.runtime_data.channels.async_refresh()
    assert hass.states.get(entity_id).state == "unavailable"


async def test_operational_channel_can_appear_after_setup(hass, client, make_entry):
    client.async_get_channels.return_value = channels_from_json({"kesselfuehler": 65})
    entry = await load(hass, make_entry)
    assert channel_id(hass, entry, "puffer_mitte") is None
    client.async_get_channels.return_value = channels_from_json({"kesselfuehler": 65, "puffer_mitte": 50})
    await entry.runtime_data.channels.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(channel_id(hass, entry, "puffer_mitte")).state) == 50


async def test_setting_readbacks_are_diagnostic_but_opted_in_controls_remain_configuration(hass, client, make_entry):
    entry = await load(hass, make_entry, writes=("4", "A1z"))
    registry = er.async_get(hass)
    for platform, category in (("sensor", EntityCategory.DIAGNOSTIC), ("number", EntityCategory.CONFIG)):
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_parameter_4")
        row = registry.async_get(entity_id)
        assert row.entity_category is category
        if platform == "sensor":
            assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert "setting readback" in row.original_name
    mode = registry.async_get_entity_id("select", DOMAIN, f"{entry.unique_id}_parameter_A1z")
    assert registry.async_get(mode).entity_category is EntityCategory.CONFIG
    client.async_set_parameter.assert_not_awaited()


async def test_unknown_channel_names_do_not_get_guessed_units_or_primary_categories(hass, client, make_entry):
    client.async_get_channels.return_value = channels_from_json({"unknown.room_temperature": 22})
    entry = await load(hass, make_entry)
    assert channel_id(hass, entry, "unknown.room_temperature") is None
    assert "unknown.room_temperature" not in OPERATIONAL_CHANNELS


async def test_advanced_option_keeps_unknown_readings_raw_and_avoids_curated_duplicates(hass, client, make_entry):
    entry = await load(hass, make_entry, options={CONF_RAW_CHANNELS: True})
    registry = er.async_get(hass)
    unknown = registry.async_get(channel_id(hass, entry, "synthetic.temperature"))
    assert unknown.entity_category is EntityCategory.DIAGNOSTIC
    assert unknown.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert unknown.original_device_class is None
    boiler = registry.async_get(channel_id(hass, entry, "kesselfuehler"))
    assert boiler.entity_category is None
    assert boiler.disabled_by is None
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert sum(row.unique_id == boiler.unique_id for row in rows) == 1


@pytest.mark.parametrize("disabled", [None, er.RegistryEntryDisabler.INTEGRATION, er.RegistryEntryDisabler.USER])
async def test_upgrade_preserves_entity_ids_and_user_choices(hass, client, make_entry, disabled):
    entry = make_entry()
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_channel_kesselfuehler", config_entry=entry,
        original_name="Raw kesselfuehler", entity_category=EntityCategory.DIAGNOSTIC, disabled_by=disabled,
    )
    registry.async_update_entity(old.entity_id, name="Custom display name")
    setting = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_parameter_4", config_entry=entry,
        original_name="Circuit 1 comfort room target", disabled_by=None,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    updated = registry.async_get(old.entity_id)
    assert updated.unique_id == old.unique_id
    assert updated.entity_category is None
    assert updated.name == "Custom display name"
    assert updated.disabled_by is (er.RegistryEntryDisabler.USER if disabled is er.RegistryEntryDisabler.USER else None)
    assert updated.original_name == "Boiler water temperature"
    assert registry.async_get(setting.entity_id).entity_category is EntityCategory.DIAGNOSTIC
    assert registry.async_get(setting.entity_id).disabled_by is None  # Do not forcibly hide existing readbacks.
    assert not entry.runtime_data.enabled_writes
    client.async_set_parameter.assert_not_awaited()


async def test_upgrade_respects_disable_new_entities_preference(hass, client, make_entry):
    entry = make_entry()
    hass.config_entries.async_update_entry(entry, pref_disable_new_entities=True)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_channel_kesselfuehler", config_entry=entry,
        original_name="Raw kesselfuehler", entity_category=EntityCategory.DIAGNOSTIC,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(old.entity_id).disabled_by is not None


async def test_temperature_telemetry_does_not_depend_on_successful_settings_poll(hass, client, make_entry):
    entry = await load(hass, make_entry)
    entry.runtime_data.parameters.async_set_update_error(UpdateFailed("Synthetic settings failure"))
    await entry.runtime_data.channels.async_refresh()
    assert hass.states.get(channel_id(hass, entry, "kesselfuehler")).state == "65.0"


def test_curated_units_are_exact_name_mappings_not_energy_or_power_inferences():
    assert len(OPERATIONAL_CHANNELS) == 29
    assert OPERATIONAL_CHANNELS["Leistung"].unit == "%"
    assert OPERATIONAL_CHANNELS["Leistung"].device_class is None
    assert OPERATIONAL_CHANNELS["Pufferfuellgrad"].state_class is None
    assert all(description.state_class in (None, SensorStateClass.MEASUREMENT)
               for description in OPERATIONAL_CHANNELS.values())
    assert "Hk1Fr35RaumTempKorr" not in OPERATIONAL_CHANNELS  # Correction is not the measured temperature.


@pytest.mark.parametrize("value,expected", [
    (0, "open"), (1, "closed"), (0.0, "open"), (1.0, "closed"),
    (2, None), (-1, None), (0.5, None), (None, None),
])
async def test_door_contact_maps_only_observed_values(hass, client, make_entry, value, expected):
    client.async_get_channels.return_value = channels_from_json(
        {} if value is None else {"tuerkontakt": value},
    )
    entry = await load(hass, make_entry, options={CONF_RAW_CHANNELS: True})
    sensor = DoorContactSensor(entry)
    assert sensor.available is (expected is not None)
    assert sensor.native_value == expected
    assert sensor.device_class == "enum"
    assert sensor.options == ["open", "closed"]
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert sensor.extra_state_attributes == {"channel": "tuerkontakt", "raw_value": value}
    row_id = channel_id(hass, entry, "tuerkontakt")
    if value is not None:
        row = er.async_get(hass).async_get(row_id)
        assert row.entity_category is EntityCategory.DIAGNOSTIC
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert row.original_name == "Boiler door contact"
    else:
        assert row_id is None
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("disabled", [None, er.RegistryEntryDisabler.INTEGRATION, er.RegistryEntryDisabler.USER])
async def test_door_contact_preserves_existing_diagnostic_identity(hass, client, make_entry, disabled):
    client.async_get_channels.return_value = channels_from_json({"tuerkontakt": 0})
    entry = make_entry(options={CONF_RAW_CHANNELS: True})
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_channel_tuerkontakt", config_entry=entry,
        original_name="Raw tuerkontakt", entity_category=EntityCategory.DIAGNOSTIC, disabled_by=disabled,
    )
    registry.async_update_entity(old.entity_id, name="My door contact")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    row = registry.async_get(old.entity_id)
    assert row.unique_id == old.unique_id
    assert row.name == "My door contact"
    assert row.original_name == "Boiler door contact"
    assert row.disabled_by is disabled
    assert row.entity_category is EntityCategory.DIAGNOSTIC
    assert sum(item.unique_id == row.unique_id for item in
               er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    if disabled is None:
        state = hass.states.get(old.entity_id)
        assert state.state == "open"
        assert state.attributes["device_class"] == "enum"
        assert state.attributes["options"] == ["open", "closed"]
        assert state.attributes["raw_value"] == 0
        client.async_get_channels.return_value = channels_from_json({"tuerkontakt": 1})
        await entry.runtime_data.channels.async_refresh()
        assert hass.states.get(old.entity_id).state == "closed"
        client.async_get_channels.return_value = channels_from_json({"tuerkontakt": 2})
        await entry.runtime_data.channels.async_refresh()
        assert hass.states.get(old.entity_id).state == "unavailable"
        client.async_get_channels.return_value = channels_from_json({})
        await entry.runtime_data.channels.async_refresh()
        assert hass.states.get(old.entity_id).state == "unavailable"
        client.async_get_channels.return_value = channels_from_json({"tuerkontakt": 0})
        await entry.runtime_data.channels.async_refresh()
        assert hass.states.get(old.entity_id).state == "open"
        entry.runtime_data.channels.async_set_update_error(UpdateFailed("Synthetic poll failure"))
        assert hass.states.get(old.entity_id).state == "unavailable"
    else:
        assert hass.states.get(old.entity_id) is None
    assert not entry.runtime_data.enabled_writes
    client.async_set_parameter.assert_not_awaited()


async def test_door_contact_remains_optional_diagnostic(hass, client, make_entry):
    client.async_get_channels.return_value = channels_from_json({"tuerkontakt": 1})
    entry = await load(hass, make_entry)
    assert channel_id(hass, entry, "tuerkontakt") is None
    assert "tuerkontakt" not in OPERATIONAL_CHANNELS


@pytest.mark.parametrize("value,expected", [
    (0, "off / ready"), (1, "ignition preparation"), (2, "ignition monitoring"),
    (3, "ignition pause"), (4, "transition to power burning"), (5, "startup / heating up"),
    (6, "power burning"), (7, "ember maintenance"), (8, "burnout"), (9, "de-ashing"),
    (10, "residual heat"), (11, "manual"), (12, "overtemperature"),
    (13, "ember-maintenance lockout"), (6.0, "power burning"),
    (14, None), (253, None), (-1, None), (6.5, None), (None, None),
])
async def test_boiler_state_maps_only_defined_codes(hass, client, make_entry, value, expected):
    client.async_get_channels.return_value = channels_from_json(
        {} if value is None else {"KesselZustand": value},
    )
    entry = await load(hass, make_entry, options={CONF_RAW_CHANNELS: True})
    sensor = BoilerStateSensor(entry)
    assert sensor.available is (expected is not None)
    assert sensor.native_value == expected
    assert sensor.device_class == "enum"
    assert len(sensor.options) == len(set(sensor.options)) == 14
    if expected is not None:
        assert expected in sensor.options
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert sensor.extra_state_attributes == {"channel": "KesselZustand", "raw_value": value}
    row_id = channel_id(hass, entry, "KesselZustand")
    if value is not None:
        row = er.async_get(hass).async_get(row_id)
        assert row.entity_category is EntityCategory.DIAGNOSTIC
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert row.original_name == "Boiler state"
    else:
        assert row_id is None
    client.async_set_parameter.assert_not_awaited()


@pytest.mark.parametrize("disabled", [None, er.RegistryEntryDisabler.INTEGRATION, er.RegistryEntryDisabler.USER])
async def test_boiler_state_preserves_existing_diagnostic_identity(hass, client, make_entry, disabled):
    client.async_get_channels.return_value = channels_from_json({"KesselZustand": 0})
    entry = make_entry(options={CONF_RAW_CHANNELS: True})
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_channel_KesselZustand", config_entry=entry,
        original_name="Raw KesselZustand", entity_category=EntityCategory.DIAGNOSTIC, disabled_by=disabled,
    )
    registry.async_update_entity(old.entity_id, name="My boiler state")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    row = registry.async_get(old.entity_id)
    assert row.unique_id == old.unique_id
    assert row.name == "My boiler state"
    assert row.original_name == "Boiler state"
    assert row.disabled_by is disabled
    assert row.entity_category is EntityCategory.DIAGNOSTIC
    assert sum(item.unique_id == row.unique_id for item in
               er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    if disabled is None:
        for value, expected in [
            (0, "off / ready"), (2, "ignition monitoring"), (5, "startup / heating up"),
            (6, "power burning"), (8, "burnout"), (10, "residual heat"),
            (12, "overtemperature"), (14, "unavailable"), (6.5, "unavailable"),
            (None, "unavailable"), (0, "off / ready"),
        ]:
            client.async_get_channels.return_value = channels_from_json(
                {} if value is None else {"KesselZustand": value},
            )
            await entry.runtime_data.channels.async_refresh()
            state = hass.states.get(old.entity_id)
            assert state.state == expected
            assert state.attributes["device_class"] == "enum"
            if expected != "unavailable":
                assert expected in state.attributes["options"]
                assert state.attributes["raw_value"] == value
                assert state.attributes["channel"] == "KesselZustand"
        entry.runtime_data.channels.async_set_update_error(UpdateFailed("Synthetic poll failure"))
        assert hass.states.get(old.entity_id).state == "unavailable"
    else:
        assert hass.states.get(old.entity_id) is None
    assert not entry.runtime_data.enabled_writes
    client.async_set_parameter.assert_not_awaited()


async def test_boiler_state_remains_optional_and_is_discovered_later(hass, client, make_entry):
    client.async_get_channels.return_value = channels_from_json({"KesselZustand": 6})
    entry = await load(hass, make_entry)
    assert channel_id(hass, entry, "KesselZustand") is None
    assert "KesselZustand" not in OPERATIONAL_CHANNELS
    await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={CONF_RAW_CHANNELS: True})
    client.async_get_channels.return_value = channels_from_json({})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert channel_id(hass, entry, "KesselZustand") is None
    client.async_get_channels.return_value = channels_from_json({"KesselZustand": 6})
    await entry.runtime_data.channels.async_refresh()
    await hass.async_block_till_done()
    row = er.async_get(hass).async_get(channel_id(hass, entry, "KesselZustand"))
    assert row.original_name == "Boiler state"
    assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_old_room_default_stays_disabled_when_current_reading_is_sentinel(hass, client, make_entry):
    client.async_get_channels.return_value = channels_from_json({"Hk1Fr35RaumTemp": -1})
    entry = make_entry()
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.unique_id}_channel_Hk1Fr35RaumTemp", config_entry=entry,
        original_name="Raw Hk1Fr35RaumTemp", entity_category=EntityCategory.DIAGNOSTIC,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(old.entity_id).entity_category is None
    assert registry.async_get(old.entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(old.entity_id) is None
    client.async_set_parameter.assert_not_awaited()
