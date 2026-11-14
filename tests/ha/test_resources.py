# SPDX-License-Identifier: Apache-2.0
"""Packaging, translations and action resource contracts."""

import json
from pathlib import Path

from homeassistant.helpers import translation

from custom_components.hargassner_hv.const import CONTROL_LABELS, DOMAIN, VERSION
from hvclient import PARAMETER_WRITES


def test_manifest_and_hacs_layout_use_an_external_sdk_requirement():
    root = Path(__file__).resolve().parents[2]
    integration = root / "custom_components" / DOMAIN
    manifest = json.loads((integration / "manifest.json").read_text())
    hacs = json.loads((root / "hacs.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["version"] == VERSION == "0.3.0"
    assert manifest["requirements"] == ["hargassner-hvclient==0.1.0"]
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert manifest["documentation"].startswith("https://")
    assert manifest["issue_tracker"].startswith("https://")
    assert hacs["homeassistant"] == "2026.11.1"
    assert not (integration / "hvclient").exists()
    assert set(CONTROL_LABELS) == set(PARAMETER_WRITES)


def test_english_resources_match_source_strings():
    root = Path(__file__).resolve().parents[2] / "custom_components" / DOMAIN
    assert json.loads((root / "strings.json").read_text()) == json.loads((root / "translations/en.json").read_text())


def test_ha_facing_text_describes_service_safety_not_authentication_flags():
    root = Path(__file__).resolve().parents[2]
    integration = root / "custom_components" / DOMAIN
    for path in (integration / "README.md", integration / "strings.json",
                 integration / "translations/en.json", root / "docs/home-assistant.md"):
        text = path.read_text().casefold()
        assert "daqonly" not in text
        assert "daq_only" not in text
        assert "service mode" in text
    overview = (root / "README.md").read_text().split("## Home Assistant integration", 1)[1].split("## Research tools", 1)[0].casefold()
    assert "daqonly" not in overview
    assert "daq_only" not in overview
    assert "service mode" in overview


async def test_real_ha_loads_custom_english_translations(hass):
    config = await translation.async_get_translations(hass, "en", "config", {DOMAIN})
    assert config[f"component.{DOMAIN}.config.step.user.data.host"] == "Host"
    options = await translation.async_get_translations(hass, "en", "options", {DOMAIN})
    assert f"component.{DOMAIN}.options.step.init.data.enabled_writes" in options
    entities = await translation.async_get_translations(hass, "en", "entity", {DOMAIN})
    assert entities[f"component.{DOMAIN}.entity.climate.circuit.state_attributes.preset_mode.state.once_comfort"] == "One-time comfort"
