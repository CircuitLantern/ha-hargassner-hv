# SPDX-License-Identifier: MPL-2.0
"""Packaging, translations and action resource contracts."""

import json
from pathlib import Path

from homeassistant.helpers import translation

from custom_components.hargassner_hv.const import CONTROL_LABELS, DOMAIN
from hvclient import PARAMETER_WRITES


def test_manifest_and_hacs_layout_use_an_external_sdk_requirement():
    root = Path(__file__).resolve().parents[2]
    integration = root / "custom_components" / DOMAIN
    manifest = json.loads((integration / "manifest.json").read_text())
    hacs = json.loads((root / "hacs.json").read_text())
    assert manifest["domain"] == DOMAIN
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


async def test_real_ha_loads_custom_english_translations(hass):
    config = await translation.async_get_translations(hass, "en", "config", {DOMAIN})
    assert config[f"component.{DOMAIN}.config.step.user.data.host"] == "Host"
    options = await translation.async_get_translations(hass, "en", "options", {DOMAIN})
    assert f"component.{DOMAIN}.options.step.init.data.enabled_writes" in options
