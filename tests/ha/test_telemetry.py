# SPDX-License-Identifier: Apache-2.0
"""Operational readings are primary sensors, not setting readbacks or debug data."""

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hargassner_hv.const import CONF_RAW_CHANNELS, DOMAIN
from custom_components.hargassner_hv.hvclient.models import channels_from_json
from custom_components.hargassner_hv.sensor import OperationalChannelSensor
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
