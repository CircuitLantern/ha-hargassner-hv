# SPDX-License-Identifier: MPL-2.0

"""Offline regressions for opt-in gating and the live suite's extra request guards."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from yarl import URL

from hvclient import AsyncHargassnerClient
from tests import test_library_live
from tests.library_support import Device, schedule_catalog
from tests.live_support import (
    LiveConfig,
    LiveRequestBlocked,
    LiveRequestGuard,
    PreviewOnlyClient,
    live_config,
)


class LiveConfigurationTests(unittest.TestCase):
    def test_explicit_flag_and_host_are_both_required_without_default_destination(self):
        with patch("tests.live_support.AsyncHargassnerClient") as client:
            for environment in ({}, {"HVCLIENT_LIVE_HOST": "example.invalid"},
                                {"HVCLIENT_LIVE": "true"}, {"HVCLIENT_LIVE": "0"}):
                self.assertIsNone(live_config(environment))
            client.assert_not_called()
        with self.assertRaises(ValueError):
            live_config({"HVCLIENT_LIVE": "1"})
        config = live_config({"HVCLIENT_LIVE": "1", "HVCLIENT_LIVE_HOST": "example.invalid"})
        self.assertEqual(config.host, "example.invalid")
        self.assertNotIn("example.invalid", repr(config))

    def test_ci_parallel_and_invalid_host_configuration_fail_before_session_creation(self):
        for extra in ({"CI": "true"}, {"PYTEST_XDIST_WORKER": "gw0"},
                      {"HVCLIENT_LIVE_HOST": "http://example.invalid"},
                      {"HVCLIENT_LIVE_HOST": "example.invalid:80"}):
            with self.subTest(extra=extra), patch("aiohttp.ClientSession") as session:
                with self.assertRaises(ValueError):
                    live_config({"HVCLIENT_LIVE": "1", "HVCLIENT_LIVE_HOST": "example.invalid", **extra})
                session.assert_not_called()

    def test_authentication_requires_exact_true_and_never_retains_secrets(self):
        guard = LiveRequestGuard(LiveConfig("example.invalid"))
        for body in (None, {}, {"key": "a" * 128}, {"key": "a" * 128, "daqOnly": False},
                     {"key": "a" * 128, "daqOnly": 1},
                     {"key": "a" * 128, "daqOnly": True, "extra": True}):
            with self.subTest(body=body), self.assertRaises(LiveRequestBlocked):
                guard.check_request("POST", "/authenticate", body)
        guard.check_request("POST", "/authenticate", {"key": "a" * 128, "daqOnly": True})
        self.assertEqual(guard.authentication_checks, 1)
        self.assertEqual(guard.requests, {})
        self.assertNotIn("a" * 128, repr(vars(guard)))

    def test_only_exact_read_roots_and_name_selection_bodies_are_allowed(self):
        guard = LiveRequestGuard(LiveConfig("example.invalid"))
        for path in ("/challenge", "/info", "/pcb", "/channels", "/parameters"):
            guard.check_request("GET", path, None)
        guard.check_request("GET", "/parameters", {"parameters": ["4"]})
        for method, path, body in (
            ("POST", "/parameters", {"parameters": [{"name": "4", "value": 21}]}),
            ("GET", "/factory-reset", None), ("GET", "/info/systemid/generate", None),
            ("GET", "/info/", None), ("PUT", "/parameters", None),
            ("GET", "/info", {}), ("GET", "/parameters", {"parameters": [4]}),
            ("GET", "/parameters", {"parameters": [], "extra": True}),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(LiveRequestBlocked):
                guard.check_request(method, path, body)


class LiveGuardAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_discovery_never_constructs_a_session(self):
        case = test_library_live.LiveSmokeTests("test_reads_and_previews_without_parameter_writes")
        with patch("tests.test_library_live.os.environ", {}), patch("aiohttp.ClientSession") as session:
            with self.assertRaises(unittest.SkipTest):
                await case.test_reads_and_previews_without_parameter_writes()
            session.assert_not_called()

    async def test_transport_blocks_other_destinations_queries_and_mutating_routes(self):
        guard = LiveRequestGuard(LiveConfig("example.invalid"))
        for method, url in (
            ("POST", "http://example.invalid/parameters"),
            ("GET", "http://example.invalid/info/systemid/generate"),
            ("GET", "http://other.invalid/info"), ("GET", "http://example.invalid:81/info"),
            ("GET", "https://example.invalid/info"), ("GET", "http://example.invalid/info?x=1"),
            ("GET", "http://user:secret@example.invalid/info"),
        ):
            with self.subTest(method=method, url=url), self.assertRaises(LiveRequestBlocked):
                await guard.on_request_start(None, None, SimpleNamespace(method=method, url=URL(url)))
        await guard.on_request_start(None, None, SimpleNamespace(method="GET", url=URL("http://example.invalid/info")))
        self.assertEqual(guard.requests, {("GET", "/info"): 1})

    async def test_setter_and_request_guard_block_before_library_transport(self):
        config = LiveConfig("example.invalid")
        client = PreviewOnlyClient(config, AsyncMock(), LiveRequestGuard(config))
        with patch.object(AsyncHargassnerClient, "_request", new_callable=AsyncMock) as transport:
            with self.assertRaises(LiveRequestBlocked):
                await client.async_set_parameter("4", 21, expected=20.5)
            with self.assertRaises(LiveRequestBlocked):
                await client._request("POST", "/parameters", {"parameters": [{"name": "4", "value": 21}]})
            with self.assertRaises(LiveRequestBlocked):
                await client._request("POST", "/authenticate", {"key": "a" * 128, "daqOnly": False})
            transport.assert_not_awaited()
        await client.async_close()

    async def test_transport_guard_blocks_even_when_wrapper_is_bypassed(self):
        guard = LiveRequestGuard(LiveConfig("127.0.0.1", 1))
        async with aiohttp.ClientSession(trace_configs=[guard.trace_config()]) as session:
            with self.assertRaises(LiveRequestBlocked):
                await session.post("http://127.0.0.1:1/parameters", json={"private": "body"})
        self.assertEqual(guard.requests, {})

    async def test_smoke_workflow_against_loopback_device_only(self):
        for profile in ("daily", "weekly", "no active controls"):
            with self.subTest(profile=profile):
                device = Device()
                device.catalog.update(schedule_catalog(weekly=profile == "weekly"))
                if profile == "no active controls":
                    for row in device.catalog.values():
                        row["active"] = False
                app = web.Application()
                app.router.add_route("*", "/{tail:.*}", device.handle)
                runner = web.AppRunner(app, shutdown_timeout=0.2)
                await runner.setup()
                self.addAsyncCleanup(runner.cleanup)
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                config = LiveConfig("127.0.0.1", site._server.sockets[0].getsockname()[1])
                case = test_library_live.LiveSmokeTests("test_reads_and_previews_without_parameter_writes")
                with patch("tests.test_library_live.live_config", return_value=config):
                    await case.test_reads_and_previews_without_parameter_writes()
                self.assertEqual(len(device.auths()), 1)
                self.assertTrue(all(request["body"]["daqOnly"] is True for request in device.auths()))
                self.assertFalse(device.posts())
                reads = [r for r in device.requests if r["path"] == "/parameters"]
                self.assertEqual(len(reads), 1 if profile == "no active controls" else 7)

    async def test_failure_messages_do_not_include_device_response_details(self):
        case = test_library_live.LiveSmokeTests("test_reads_and_previews_without_parameter_writes")
        with (
            patch("tests.test_library_live.live_config", return_value=LiveConfig("example.invalid")),
            patch.object(PreviewOnlyClient, "async_get_info", side_effect=ValueError("private-serial-cookie")),
            self.assertRaises(AssertionError) as caught,
        ):
            await case.test_reads_and_previews_without_parameter_writes()
        self.assertIn("info read", str(caught.exception))
        self.assertNotIn("private-serial-cookie", str(caught.exception))
