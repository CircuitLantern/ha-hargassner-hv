# SPDX-License-Identifier: MPL-2.0

"""Opt-in hardware smoke test. Default discovery skips without opening a socket."""

import os
import unittest

import aiohttp

from hvclient import (
    CIRCUIT_MODE_PARAMETERS,
    CIRCUIT_SCHEDULE_PARAMETERS,
    HEATING_CURVE_WRITES,
    NUMERIC_USER_WRITES,
    Choice,
    DailySchedule,
    ValidationError,
    Weekday,
    WeeklySchedule,
    WeeklyTimeRanges,
)
from tests.live_support import LiveRequestGuard, PreviewOnlyClient, live_config


class LiveSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_and_previews_without_parameter_writes(self):
        config = live_config(os.environ)
        if config is None:
            self.skipTest("live tests require HVCLIENT_LIVE=1 and HVCLIENT_LIVE_HOST")
        guard = LiveRequestGuard(config)
        phase = "session setup"
        try:
            async with (
                aiohttp.ClientSession(
                    cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                    trace_configs=[guard.trace_config()],
                ) as session,
                PreviewOnlyClient(config, session, guard) as client,
            ):
                phase = "info read"
                await client.async_get_info()
                phase = "channel read"
                await client.async_get_channels()
                phase = "PCB read"
                await client.async_get_pcb()
                phase = "parameter catalog"
                parameters = await client.async_get_parameters()
                self.assertTrue(bool(parameters), "supported catalog is empty")
                tracked = set()

                async def preview(name, requested, expected):
                    result = await client.async_preview_parameter_write(
                        name, requested, expected=expected,
                    )
                    self.assertTrue(result.parameter.name == name, "preview name differs")
                    tracked.add(name)
                    return result

                # Discover eligible entries; optional circuits need not exist.
                # Current-value previews do not prescribe installation settings.
                phase = "temperature preview"
                temperatures = [parameters[name] for name in NUMERIC_USER_WRITES
                                if name in parameters and parameters[name].active is True]
                if temperatures:
                    parameter = temperatures[0]
                    await preview(parameter.name, parameter.value, parameter.value)

                phase = "curve preview"
                curves = [parameters[name] for name in HEATING_CURVE_WRITES
                          if name in parameters and parameters[name].active is True]
                if curves:
                    parameter = curves[0]
                    await preview(parameter.name, parameter.value, parameter.value)

                phase = "program discovery"
                programs = []
                for circuit in CIRCUIT_SCHEDULE_PARAMETERS.values():
                    active = [program for program in (circuit.daily, circuit.weekly)
                              if program.name in parameters
                              and parameters[program.name].active is True]
                    self.assertTrue(len(active) <= 1, "both circuit program variants are active")
                    programs.extend(active)
                if programs:
                    program = programs[0]
                    phase = "circuit mode preview"
                    mode_name = CIRCUIT_MODE_PARAMETERS[program.zone].name
                    mode = parameters[mode_name]
                    self.assertTrue(isinstance(mode.value, Choice), "mode is not a choice")
                    await preview(mode_name, mode.value.id, mode.value.id)

                    phase = "active program preview"
                    parameter = parameters[program.name]
                    result = await preview(program.name, parameter.value, parameter.value)
                    self.assertTrue(result.parameter.schedule_limits is program.limits,
                                    "public program limits differ")

                    phase = "inactive program refusal"
                    # Use a valid replacement so validation reaches live context,
                    # rather than failing on malformed stored padding/day groups.
                    unused = (WeeklySchedule((WeeklyTimeRanges(tuple(Weekday), ()),))
                              if program.kind == "daily" else DailySchedule())
                    with self.assertRaises(ValidationError):
                        await client.async_preview_parameter_write(program.peer, unused)

                phase = "repeat selected reads"
                if tracked:
                    after = await client.async_get_parameters(sorted(tracked))
                    self.assertTrue(
                        all(after[name].value == parameters[name].value for name in tracked),
                        "previewed settings changed; local UI/another client or an automatic "
                        "mode transition may be responsible; do not retry/restore automatically",
                    )
            phase = "request audit"
            self.assertTrue(guard.authentication_checks > 0, "no checked authentication")
            self.assertTrue(guard.authentication_checks == guard.requests["POST", "/authenticate"],
                            "authentication request audit differs")
            self.assertTrue(guard.requests["POST", "/parameters"] == 0, "parameter POST observed")
        except Exception as exc:  # noqa: BLE001 -- redact all device/transport failure details
            # Default unittest/pytest failures must not print response documents,
            # private identifiers, schedule values, challenges, or auth cookies.
            raise AssertionError(
                f"Live smoke failed during {phase} ({type(exc).__name__}); "
                "response and authentication details suppressed"
            ) from None
