# SPDX-License-Identifier: Apache-2.0
"""Shared SDK client, coordinator polling, and verified control operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from hvclient import (
    CIRCUIT_MODE_PARAMETERS,
    CIRCUIT_SCHEDULE_PARAMETERS,
    AsyncHargassnerClient,
    Choice,
    HargassnerError,
    Parameter,
    SystemInfo,
    ValidationError,
    WriteMismatchError,
    WriteVerificationError,
)

from .const import CONF_IDENTITY_SOURCE, DOMAIN
from .identity import IdentityError, identity

_LOGGER = logging.getLogger(__name__)
_UNSET = object()


@dataclass(frozen=True)
class ParameterData:
    info: SystemInfo = field(repr=False)
    parameters: Mapping[str, Parameter] = field(repr=False)


class HargassnerCoordinator(DataUpdateCoordinator[Any]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, name: str,
        fetch: Callable[[], Awaitable[Any]], seconds: int,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {name}",
                         update_interval=timedelta(seconds=seconds))
        self._fetch = fetch
        # Start time, not completion time: a slow old poll must not supersede a
        # verified write that completed while that poll was in flight.
        self.observed_at = 0.0

    async def _async_update_data(self) -> Any:
        try:
            started = monotonic()
            result = await self._fetch()
            self.observed_at = started
            return result
        except IdentityError:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="identity_changed") from None
        except HargassnerError:
            # There are no user credentials to re-enter. Let normal polling
            # recover bounded session failures instead of starting login loops.
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="read_failed") from None


class ParameterCoordinator(HargassnerCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AsyncHargassnerClient):
        self.client = client
        self.entry = entry
        super().__init__(hass, entry, "parameters", self._fetch_parameters, 300)

    async def verify_identity(self) -> SystemInfo:
        info = await self.client.async_get_info()
        _, key = identity(info, self.entry.data[CONF_IDENTITY_SOURCE])
        if key != self.entry.unique_id:
            raise IdentityError("Device identity differs")
        return info

    async def _fetch_parameters(self) -> ParameterData:
        info = await self.verify_identity()
        return ParameterData(info, await self.client.async_get_parameters())


@dataclass
class RuntimeData:
    client: AsyncHargassnerClient = field(repr=False)
    parameters: ParameterCoordinator = field(repr=False)
    channels: HargassnerCoordinator = field(repr=False)
    pcb: HargassnerCoordinator | None = field(repr=False)
    enabled_writes: frozenset[str]
    raw_channels: bool
    closed: bool = False
    _operations: set[asyncio.Task] = field(default_factory=set, repr=False)
    _control_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    mode_readbacks: dict[str, tuple[int, float]] = field(default_factory=dict, repr=False)

    @property
    def info(self) -> SystemInfo:
        return self.parameters.data.info

    def parameter(self, name: str) -> Parameter | None:
        data = self.parameters.data
        return data.parameters.get(name) if data is not None else None

    def circuit_present(self, zone: str) -> bool:
        programs = CIRCUIT_SCHEDULE_PARAMETERS[zone]
        return any((parameter := self.parameter(program.name)) is not None
                   and parameter.active is True for program in (programs.daily, programs.weekly))

    async def cancel_operations(self) -> None:
        self.closed = True
        tasks = [task for task in self._operations if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def _operation(self, name: str):
        if self.closed or name not in self.enabled_writes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="writes_disabled")
        if not self.parameters.last_update_success or self.parameter(name) is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="state_unavailable")
        task = asyncio.current_task()
        if task is not None:
            self._operations.add(task)
        try:
            async with self._control_lock:
                # Queued controls may outlive their permission/state snapshot.
                if self.closed or name not in self.enabled_writes:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="writes_disabled")
                if not self.parameters.last_update_success or self.parameter(name) is None:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="state_unavailable")
                yield
        finally:
            if task is not None:
                self._operations.discard(task)

    async def async_control(
        self, name: str, requested: Any, *, expected: Any = _UNSET, preview: bool = False,
        guard: Callable[[], Awaitable[None]] | None = None,
    ) -> Any:
        """One SDK call; never retry a write, roll back, or optimistically update."""
        async with self._operation(name):
            parameter = self.parameter(name)
            assert parameter is not None
            if expected is _UNSET and not preview:
                expected = parameter.value.id if isinstance(parameter.value, Choice) else parameter.value
            try:
                await self.parameters.verify_identity()
                if guard is not None:
                    await guard()
                if preview:
                    kwargs = {} if expected is _UNSET else {"expected": expected}
                    return await self.client.async_preview_parameter_write(name, requested, **kwargs)
                result = await self.client.async_set_parameter(name, requested, expected=expected)
            except WriteVerificationError as error:
                self.mode_readbacks.pop(name, None)
                await self.parameters.async_request_refresh()
                key = "write_mismatch" if isinstance(error, WriteMismatchError) else "write_unknown"
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key=key) from None
            except IdentityError:
                self.parameters.async_set_update_error(
                    UpdateFailed(translation_domain=DOMAIN, translation_key="identity_changed")
                )
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="change_refused") from None
            except ValidationError:
                await self.parameters.async_request_refresh()
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="change_refused") from None
            except HargassnerError:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="read_failed") from None
            # Use only the SDK's verified separate read-back, never the requested
            # value. A debounced full refresh may otherwise leave stale UI state
            # (and stale expected values) after a successful consecutive command.
            if not result.verified or result.after is None:
                self.mode_readbacks.pop(name, None)
                await self.parameters.async_request_refresh()
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="write_unknown")
            current = self.parameters.data
            parameters = dict(current.parameters)
            parameters[name] = result.after
            if (name in {definition.name for definition in CIRCUIT_MODE_PARAMETERS.values()}
                    and result.outcome == "verified"
                    and isinstance(result.after.value, Choice)):
                self.mode_readbacks[name] = (result.after.value.id, monotonic())
            self.parameters.async_set_updated_data(ParameterData(current.info, parameters))
            await self.parameters.async_request_refresh()
            return result


type HargassnerConfigEntry = ConfigEntry[RuntimeData]


def mode_zone(name: str) -> str:
    return next(zone for zone, definition in CIRCUIT_MODE_PARAMETERS.items() if definition.name == name)
