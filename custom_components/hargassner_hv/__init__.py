# SPDX-License-Identifier: Apache-2.0
"""Hargassner Smart HV adapter; all boiler protocol operations belong to hvclient."""

from contextlib import AsyncExitStack

from homeassistant.const import CONF_HOST, CONF_PORT, EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from hvclient import AsyncHargassnerClient, WritePolicy, WritePolicyError

from .const import (
    CONF_ACK_WRITES,
    CONF_ENABLED_WRITES,
    CONF_IDENTITY_SOURCE,
    CONF_PCB,
    CONF_RAW_CHANNELS,
    DOMAIN,
)
from .coordinator import (
    HargassnerConfigEntry,
    HargassnerCoordinator,
    ParameterCoordinator,
    RuntimeData,
)
from .services import async_setup_services
from .telemetry import OPERATIONAL_CHANNELS

PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.CLIMATE]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HargassnerConfigEntry) -> None:
    if not entry.unique_id or entry.data.get(CONF_IDENTITY_SOURCE) not in {"mac", "system_id", "serial"}:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="identity_changed")
    enabled = entry.options.get(CONF_ENABLED_WRITES, [])
    if (not isinstance(enabled, (list, tuple))
            or any(type(entry.options.get(flag, False)) is not bool for flag in
                   (CONF_ACK_WRITES, CONF_RAW_CHANNELS, CONF_PCB))
            or (enabled and entry.options.get(CONF_ACK_WRITES) is not True)):
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="writes_disabled")
    try:
        policy = WritePolicy.only(*enabled)
    except (WritePolicyError, TypeError, ValueError):
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="writes_disabled") from None
    async with AsyncExitStack() as stack:
        client = AsyncHargassnerClient(
            entry.data[CONF_HOST], entry.data[CONF_PORT], session=async_get_clientsession(hass),
            write_policy=policy, timeout=10, read_retries=0, authentication_attempts=2,
        )
        stack.push_async_callback(client.async_close)
        parameters = ParameterCoordinator(hass, entry, client)
        channels = HargassnerCoordinator(hass, entry, "channels", client.async_get_channels, 30)
        pcb = (HargassnerCoordinator(hass, entry, "pcb", client.async_get_pcb, 60)
               if entry.options.get(CONF_PCB, False) else None)
        for coordinator in (parameters, channels, pcb):
            if coordinator is not None:
                stack.push_async_callback(coordinator.async_shutdown)
        await parameters.async_config_entry_first_refresh()
        await channels.async_config_entry_first_refresh()
        if pcb is not None:
            # Optional diagnostics must not hide otherwise healthy telemetry.
            await pcb.async_refresh()
        runtime = RuntimeData(client, parameters, channels, pcb, frozenset(enabled),
                              bool(entry.options.get(CONF_RAW_CHANNELS, False)))
        stack.push_async_callback(runtime.cancel_operations)
        entry.runtime_data = runtime
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(stack.pop_all().aclose)


async def async_unload_entry(hass: HomeAssistant, entry: HargassnerConfigEntry) -> None:
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: HargassnerConfigEntry) -> None:
    """Reclassify readbacks and promote old raw entities without replacing IDs."""
    if entry.version != 1:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="identity_changed")
    if entry.minor_version < 2:
        registry = er.async_get(hass)
        channels = {f"{entry.unique_id}_channel_{name}" for name in OPERATIONAL_CHANNELS}
        prefix = f"{entry.unique_id}_parameter_"
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain != "sensor" or entity.platform != DOMAIN:
                continue
            if entity.unique_id.startswith(prefix):
                registry.async_update_entity(entity.entity_id, entity_category=EntityCategory.DIAGNOSTIC)
            elif entity.unique_id in channels:
                registry.async_update_entity(entity.entity_id, entity_category=None)
        hass.config_entries.async_update_entry(entry, version=1, minor_version=2)
