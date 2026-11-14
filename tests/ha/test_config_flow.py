# SPDX-License-Identifier: Apache-2.0
"""Identity, connection failures, duplicate prevention, reconfigure and opt-in UI."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hargassner_hv.const import (
    CONF_ACK_WRITES,
    CONF_ENABLED_WRITES,
    CONF_PCB,
    CONF_RAW_CHANNELS,
    DOMAIN,
)
from custom_components.hargassner_hv.hvclient import (
    AuthenticationError,
    CooldownError,
    ProtocolError,
    TransportError,
)
from custom_components.hargassner_hv.hvclient.models import system_info_from_json
from custom_components.hargassner_hv.identity import IdentityError, identities, identity


@pytest.mark.parametrize("error,key", [
    (TransportError("synthetic-secret"), "cannot_connect"),
    (AuthenticationError("synthetic-secret"), "auth_failed"),
    (CooldownError("synthetic-secret"), "cooldown"),
    (ProtocolError("synthetic-secret"), "incompatible"),
])
async def test_connection_errors_are_redacted(hass, client, error, key, caplog):
    client.async_get_info.side_effect = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={
        "host": "boiler.example", "port": 80,
    })
    assert result["type"] == "form"
    assert result["errors"] == {"base": key}
    assert "synthetic-secret" not in json.dumps(result, default=str)
    assert "synthetic-secret" not in caplog.text
    client.async_close.assert_awaited_once()
    client.async_set_parameter.assert_not_awaited()


async def test_form_has_no_default_host(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    assert result["data_schema"]({"host": "controller.example"})["port"] == 80


async def test_no_identity_never_falls_back_to_host(hass, client):
    client.async_get_info.return_value = system_info_from_json({"softwareVersion": "test"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={
        "host": "boiler.example", "port": 80,
    })
    assert result["errors"] == {"base": "no_unique_id"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize("host", [None, True, 123, {}])
async def test_malformed_host_is_rejected_without_io(hass, client, host):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={
        "host": host,
    })
    assert result["errors"] == {"base": "invalid_host"}
    client.async_get_info.assert_not_awaited()


async def test_same_device_different_host_is_duplicate(hass, client, make_entry):
    make_entry()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={
        "host": "alternate.example", "port": 80,
    })
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reconfigure_only_accepts_same_identity(hass, client, make_entry):
    entry = make_entry()
    with patch("custom_components.hargassner_hv.async_setup_entry", new_callable=AsyncMock):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id},
            data={"host": "alternate.example", "port": 81},
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "alternate.example"
    original_key = entry.unique_id
    client.async_get_info.return_value = system_info_from_json({"mac": "02:00:00:00:00:02"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id},
        data={"host": "different.example", "port": 80},
    )
    assert result["reason"] == "wrong_device"
    assert entry.data["host"] == "alternate.example"
    assert entry.unique_id == original_key
    client.async_set_parameter.assert_not_awaited()


async def test_options_require_explicit_acknowledgement_and_reload(hass, client, make_entry):
    entry = make_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    data = {CONF_ENABLED_WRITES: ["4", "A1z", "3"], CONF_ACK_WRITES: False,
            CONF_RAW_CHANNELS: False, CONF_PCB: False}
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=data)
    assert result["errors"] == {"base": "acknowledge_writes"}
    assert not entry.options[CONF_ENABLED_WRITES]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={**data, CONF_ACK_WRITES: True},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.runtime_data.enabled_writes == {"4", "A1z", "3"}
    client.async_set_parameter.assert_not_awaited()


def test_identity_is_hashed_stable_and_uses_valid_fallbacks():
    first = system_info_from_json({"mac": "02:00:00:00:00:01", "systemId": "synthetic-system"})
    second = system_info_from_json({"mac": "02-00-00-00-00-01"})
    assert identity(first) == identity(second)
    assert "02:00:00:00:00:01" not in identity(first)[1]
    assert len(identities(first)) == 2
    assert identity(system_info_from_json({"mac": "00:00:00:00:00:00", "systemId": "fallback-id"}))[0] == "system_id"
    assert identity(system_info_from_json({"articleSerialNumber": "synthetic-serial"}))[0] == "serial"
    for data in ({}, {"mac": "ff:ff:ff:ff:ff:ff"}, {"commissionNumber": "not-unique"}):
        with pytest.raises(IdentityError):
            identity(system_info_from_json(data))
