# SPDX-License-Identifier: MPL-2.0
"""Read-only connection validation, identity-safe reconfiguration, and opt-in controls."""

from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from hvclient import (
    AsyncHargassnerClient,
    AuthenticationError,
    CooldownError,
    HargassnerError,
    ProtocolError,
)

from .const import (
    CONF_ACK_WRITES,
    CONF_ENABLED_WRITES,
    CONF_IDENTITY_SOURCE,
    CONF_PCB,
    CONF_RAW_CHANNELS,
    CONTROL_LABELS,
    DOMAIN,
    NAME,
)
from .identity import IdentityError, identities, identity


class HargassnerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return HargassnerOptionsFlow()

    async def _connection_step(self, step_id: str, user_input: dict[str, Any] | None):
        errors = {}
        entry = self._get_reconfigure_entry() if step_id == "reconfigure" else None
        if user_input is not None:
            try:
                raw_host = user_input.get(CONF_HOST)
                if not isinstance(raw_host, str):
                    raise TypeError("Invalid host")
                host = raw_host.strip()
                port = user_input.get(CONF_PORT, 80)
                async with AsyncHargassnerClient(
                    host, port, session=async_get_clientsession(self.hass),
                    timeout=10, read_retries=0, authentication_attempts=2,
                ) as client:
                    info = await client.async_get_info()
                source, key = identity(info, entry.data[CONF_IDENTITY_SOURCE] if entry else None)
            except IdentityError:
                errors["base"] = "no_unique_id"
            except CooldownError:
                errors["base"] = "cooldown"
            except AuthenticationError:
                errors["base"] = "auth_failed"
            except ProtocolError:
                errors["base"] = "incompatible"
            except HargassnerError:
                errors["base"] = "cannot_connect"
            except (TypeError, ValueError):
                errors["base"] = "invalid_host"
            else:
                if entry is not None:
                    if key != entry.unique_id:
                        return self.async_abort(reason="wrong_device")
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_HOST: host, CONF_PORT: port},
                    )
                candidates = set(identities(info).values())
                if any(existing.unique_id in candidates for existing in self._async_current_entries()):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(key)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=NAME, data={CONF_HOST: host, CONF_PORT: port, CONF_IDENTITY_SOURCE: source},
                )
        defaults = user_input or (entry.data if entry is not None else {})
        default_host = defaults.get(CONF_HOST, "")
        default_port = defaults.get(CONF_PORT, 80)
        if not isinstance(default_host, str):
            default_host = ""
        if type(default_port) is not int or not 1 <= default_port <= 65535:
            default_port = 80
        return self.async_show_form(
            step_id=step_id, errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=default_host): str,
                vol.Required(CONF_PORT, default=default_port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)),
            }),
        )

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return await self._connection_step("user", user_input)

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        return await self._connection_step("reconfigure", user_input)


class HargassnerOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            enabled = user_input.get(CONF_ENABLED_WRITES, [])
            if (not isinstance(enabled, list)
                    or any(not isinstance(name, str) or name not in CONTROL_LABELS for name in enabled)
                    or any(type(user_input.get(flag, False)) is not bool for flag in
                           (CONF_ACK_WRITES, CONF_RAW_CHANNELS, CONF_PCB))
                    or set(user_input) - {CONF_ENABLED_WRITES, CONF_ACK_WRITES, CONF_RAW_CHANNELS, CONF_PCB}):
                errors["base"] = "invalid_controls"
            elif enabled and user_input.get(CONF_ACK_WRITES) is not True:
                errors["base"] = "acknowledge_writes"
            else:
                return self.async_create_entry(title="", data=user_input)
        defaults = user_input or self.config_entry.options
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=vol.Schema({
                vol.Optional(CONF_ENABLED_WRITES, default=defaults.get(CONF_ENABLED_WRITES, [])):
                    selector.SelectSelector(selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=name, label=label)
                                 for name, label in CONTROL_LABELS.items()],
                        multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)),
                vol.Required(CONF_ACK_WRITES, default=defaults.get(CONF_ACK_WRITES, False)): bool,
                vol.Required(CONF_RAW_CHANNELS, default=defaults.get(CONF_RAW_CHANNELS, False)): bool,
                vol.Required(CONF_PCB, default=defaults.get(CONF_PCB, False)): bool,
            }),
        )
