"""Async client contract tests against a real HTTP server bound only to loopback."""

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from yarl import URL

from hvclient import (
    AsyncHargassnerClient,
    AuthenticationError,
    ChannelSnapshot,
    ClientClosedError,
    CooldownError,
    HTTPError,
    ParameterAccessError,
    ParameterNotFoundError,
    PcbInfo,
    ProtocolError,
    SystemInfo,
    TransportError,
    ValidationError,
    WriteMismatchError,
    WriteOutcomeUnknownError,
    WritePolicy,
    WritePolicyError,
)
from tests.library_support import (
    AUTH_COOKIE,
    KEY,
    REFRESH_COOKIE,
    Device,
    Reply,
    numeric,
)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device = Device()
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.device.handle)
        self.runner = web.AppRunner(app, shutdown_timeout=0.2)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.port = self.site._server.sockets[0].getsockname()[1]
        self.addAsyncCleanup(self.runner.cleanup)
        self.sleep = AsyncMock()
        self.sleep_patch = patch("hvclient.client._sleep", self.sleep)
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    async def asyncTearDown(self):
        self.device.release_write.set()

    def client(self, *, write=False, **kwargs):
        policy = kwargs.pop("write_policy", WritePolicy.only("4") if write else WritePolicy())
        client = AsyncHargassnerClient(
            "127.0.0.1", self.port, write_policy=policy, **kwargs
        )
        self.addAsyncCleanup(client.async_close)
        return client

    def assert_safe_wire(self):
        self.assertTrue(self.device.auths())
        for request in self.device.auths():
            self.assertEqual(request["body"], {"key": KEY, "daqOnly": True})
            self.assertIs(request["body"]["daqOnly"], True)
        self.assertTrue(all(request["path"] in {
            "/challenge", "/authenticate", "/parameters", "/channels", "/info", "/pcb"
        }
                            for request in self.device.requests))
        for request in self.device.requests:
            if request["path"] in {"/channels", "/info", "/pcb"}:
                self.assertEqual(request["method"], "GET")
                self.assertIsNone(request["body"])

    async def test_full_catalog_returns_all_44_user_entries_only(self):
        client = self.client()
        parameters = await client.async_get_parameters()
        self.assertEqual(len(parameters), 44)
        self.assertEqual(parameters["4"].value, 20.5)
        self.assertFalse(parameters["HP4"].active)
        self.assertTrue(all(p.security_level == "USER" for p in parameters.values()))
        self.assertNotIn("installer", parameters)
        self.assertNotIn("service", parameters)
        self.assertNotIn("developer", parameters)
        self.assertIsNone(self.device.requests[-1]["body"])
        self.assert_safe_wire()

    async def test_selected_get_has_json_body_and_does_not_reauthenticate(self):
        client = self.client()
        selected = await client.async_get_parameters(["4", "5"])
        single = await client.async_get_parameter("4")
        self.assertEqual(set(selected), {"4", "5"})
        self.assertEqual(single.value, 20.5)
        gets = [r for r in self.device.requests if r["path"] == "/parameters"]
        self.assertEqual(gets[0]["body"], {"parameters": ["4", "5"]})
        self.assertEqual(gets[1]["body"], {"parameters": ["4"]})
        self.assertEqual(gets[0]["cookie"], AUTH_COOKIE)
        self.assertEqual(gets[1]["cookie"], REFRESH_COOKIE)
        self.assertEqual(len(self.device.auths()), 1)

    async def test_non_user_selections_are_refused(self):
        client = self.client()
        for name in ("installer", "service", "developer"):
            with self.subTest(name=name), self.assertRaises(ParameterAccessError):
                await client.async_get_parameter(name)
        self.assertFalse(self.device.posts())
        self.assert_safe_wire()

    async def test_unknown_level_and_missing_level_are_not_returned(self):
        self.device.catalog["unknown"] = numeric("unknown", securityLevel="UNKNOWN_LEVEL")
        self.device.catalog["missing-level"] = numeric("missing-level")
        del self.device.catalog["missing-level"]["securityLevel"]
        client = self.client()
        self.assertEqual(len(await client.async_get_parameters()), 44)
        with self.assertRaises(ParameterAccessError):
            await client.async_get_parameter("missing-level")

    async def test_non_user_values_are_not_decoded_or_exposed(self):
        self.device.catalog["installer"]["value"] = {"invalid": "numeric value"}
        self.device.catalog["4"]["children"] = [self.device.catalog["installer"]]
        parameter = (await self.client().async_get_parameters())["4"]
        self.assertNotIn("children", parameter.to_dict())

    async def test_missing_selected_parameter(self):
        with self.assertRaises(ParameterNotFoundError):
            await self.client().async_get_parameter("missing")

    async def test_empty_selection_makes_no_request(self):
        client = self.client()
        self.assertEqual(await client.async_get_parameters([]), {})
        self.assertIsNone(client._session)
        self.assertFalse(self.device.requests)

    async def test_invalid_selections_fail_before_io(self):
        client = self.client()
        for names in ("4", 4, [""], ["4", "4"], [None], ["bad\nname"],
                      [str(index) for index in range(257)]):
            with self.subTest(names=names), self.assertRaises(ValidationError):
                await client.async_get_parameters(names)
        self.assertFalse(self.device.requests)

    async def test_close_unused_client_has_no_http_side_effect(self):
        client = self.client()
        await client.async_close()
        await client.async_close()
        self.assertIsNone(client._session)
        self.assertFalse(self.device.requests)

    async def test_read_only_default_refuses_before_io(self):
        client = self.client()
        with self.assertRaises(WritePolicyError):
            await client.async_set_parameter("4", 21)
        with self.assertRaises(WritePolicyError):
            await client.async_preview_parameter_write("4", 21)
        self.assertFalse(self.device.requests)

    async def test_unreviewed_user_controls_cannot_be_enabled(self):
        for name in ("E18", "Setup5", "Setup6", "mode.remote_control.enabled", "installer"):
            with self.subTest(name=name), self.assertRaises(WritePolicyError):
                WritePolicy.only(name)

    async def test_preview_reads_and_validates_without_post(self):
        client = self.client(write=True)
        preview = await client.async_preview_parameter_write("4", 21, expected=20.5)
        self.assertEqual(preview.parameter.value, 20.5)
        self.assertEqual(preview.requested, 21)
        self.assertFalse(self.device.posts())

    async def test_numeric_write_uses_sparse_entry_and_separate_get(self):
        result = await self.client(write=True).async_set_parameter("4", 21, expected=20.5)
        self.assertTrue(result.verified)
        self.assertTrue(result.read_back_matches)
        self.assertEqual(result.outcome, "verified")
        self.assertEqual(result.before.value, 20.5)
        self.assertEqual(result.returned.value, 21)
        self.assertEqual(result.after.value, 21)
        self.assertEqual(result.write_status, 200)
        self.assertEqual(len(self.device.posts()), 1)
        self.assertEqual(self.device.posts()[0]["body"], {
            "parameters": [{"name": "4", "value": 21.0}]
        })
        self.assertEqual(self.device.requests[-1]["method"], "GET")
        self.assert_safe_wire()

    async def test_unchanged_value_never_posts(self):
        result = await self.client(write=True).async_set_parameter("4", 20.5)
        self.assertEqual(result.outcome, "unchanged")
        self.assertTrue(result.verified)
        self.assertIsNone(result.returned)
        self.assertIsNone(result.write_status)
        self.assertFalse(self.device.posts())

    async def test_zero_and_negative_integer_are_not_omitted(self):
        client = self.client(write_policy=WritePolicy.only("4", "11"))
        result = await client.async_set_parameter("4", 0)
        self.assertEqual(result.after.value, 0)
        result = await client.async_set_parameter("11", -5, expected=10)
        self.assertEqual(result.after.value, -5)
        self.assertIs(type(self.device.posts()[-1]["body"]["parameters"][0]["value"]), int)

    async def test_expected_value_mismatch_is_not_a_write(self):
        with self.assertRaises(ValidationError):
            await self.client(write=True).async_set_parameter("4", 21, expected=19)
        self.assertFalse(self.device.posts())

    async def test_validation_uses_current_metadata_every_time(self):
        client = self.client(write=True)
        await client.async_preview_parameter_write("4", 21)
        self.device.catalog["4"]["active"] = False
        with self.assertRaises(ValidationError):
            await client.async_set_parameter("4", 21)
        self.assertFalse(self.device.posts())

    async def test_type_or_security_change_blocks_write(self):
        client = self.client(write=True)
        self.device.catalog["4"].update(type="INTEGER", value=20, increment=1)
        with self.assertRaises(WritePolicyError):
            await client.async_set_parameter("4", 21)
        self.device.catalog["4"]["securityLevel"] = "PLUMBER"
        with self.assertRaises(ParameterAccessError):
            await client.async_set_parameter("4", 21)
        self.assertFalse(self.device.posts())

    async def test_silent_200_rejection_is_mismatch(self):
        self.device.silent_rejection = True
        with self.assertRaises(WriteMismatchError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        result = caught.exception.result
        self.assertFalse(result.verified)
        self.assertFalse(result.read_back_matches)
        self.assertEqual(result.after.value, 20.5)
        self.assertEqual(result.write_status, 200)
        self.assertEqual(len(self.device.posts()), 1)

    async def test_successful_post_but_mismatched_fresh_get_is_failure(self):
        before = copy.deepcopy(self.device.catalog["4"])
        self.device.read_replies = [Reply(document={"parameters": [before]})] * 2
        with self.assertRaises(WriteMismatchError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        self.assertEqual(caught.exception.result.returned.value, 21)
        self.assertEqual(caught.exception.result.after.value, 20.5)
        self.assertEqual(len(self.device.posts()), 1)

    async def test_lost_post_response_reads_back_but_is_never_replayed(self):
        self.device.disconnect_after_write = True
        with self.assertRaises(WriteOutcomeUnknownError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        self.assertTrue(caught.exception.result.read_back_matches)
        self.assertIsInstance(caught.exception.write_error, TransportError)
        self.assertEqual(caught.exception.result.outcome, "unknown")
        self.assertEqual(len(self.device.posts()), 1)

    async def test_timed_out_write_is_not_replayed(self):
        self.device.write_delay = 0.4
        with self.assertRaises(WriteOutcomeUnknownError) as caught:
            await self.client(write=True, timeout=0.15).async_set_parameter("4", 21)
        self.assertTrue(caught.exception.result.read_back_matches)
        self.assertEqual(len(self.device.posts()), 1)

    async def test_write_401_never_reauthenticates_to_replay(self):
        self.device.write_reply = Reply(status=401)
        with self.assertRaises(WriteOutcomeUnknownError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        self.assertEqual(caught.exception.write_error.status, 401)
        self.assertEqual(len(self.device.posts()), 1)
        self.assertEqual(len(self.device.auths()), 1)

    async def test_write_400_still_reconciles(self):
        self.device.write_reply = Reply(status=400)
        with self.assertRaises(WriteOutcomeUnknownError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        self.assertTrue(caught.exception.result.read_back_matches)
        self.assertEqual(caught.exception.result.write_status, 400)
        self.assertEqual(len(self.device.posts()), 1)

    async def test_malformed_write_response_does_not_hide_readback(self):
        self.device.write_reply = Reply(raw=b"not JSON")
        with self.assertRaises(WriteOutcomeUnknownError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        self.assertIsInstance(caught.exception.write_error, ProtocolError)
        self.assertTrue(caught.exception.result.read_back_matches)
        self.assertEqual(len(self.device.posts()), 1)

    async def test_missing_readback_does_not_claim_success(self):
        before = copy.deepcopy(self.device.catalog["4"])
        self.device.read_replies = [Reply(document={"parameters": [before]}), Reply(raw=b"bad")]
        with self.assertRaises(WriteOutcomeUnknownError) as caught:
            await self.client(write=True).async_set_parameter("4", 21)
        self.assertEqual(caught.exception.result.returned.value, 21)
        self.assertIsNone(caught.exception.result.after)
        self.assertIsInstance(caught.exception.read_error, ProtocolError)

    async def test_read_401_reauthenticates_once_with_true(self):
        self.device.read_replies = [Reply(status=401)]
        self.assertEqual((await self.client().async_get_parameter("4")).value, 20.5)
        self.assertEqual(len(self.device.auths()), 2)
        self.assert_safe_wire()

    async def test_readback_401_recovery_does_not_replay_post(self):
        before = copy.deepcopy(self.device.catalog["4"])
        self.device.read_replies = [Reply(document={"parameters": [before]}), Reply(status=401)]
        result = await self.client(write=True).async_set_parameter("4", 21)
        self.assertTrue(result.verified)
        self.assertEqual(len(self.device.auths()), 2)
        self.assertEqual(len(self.device.posts()), 1)
        self.assert_safe_wire()

    async def test_repeated_read_401_is_bounded(self):
        self.device.read_replies = [Reply(status=401)] * 2
        with self.assertRaises(AuthenticationError):
            await self.client().async_get_parameter("4")
        self.assertEqual(len(self.device.auths()), 2)
        self.assert_safe_wire()

    async def test_auth_400_retry_and_cooldown_stay_true(self):
        self.device.auth_statuses = [400]
        await self.client().async_get_parameter("4")
        self.assertEqual(len(self.device.auths()), 2)
        self.sleep.assert_awaited()
        self.assert_safe_wire()

    async def test_auth_rejections_exhaust_without_stronger_login(self):
        self.device.auth_statuses = [400] * 3
        with self.assertRaises(HTTPError):
            await self.client().async_get_parameter("4")
        self.assertEqual(len(self.device.auths()), 3)
        self.assertFalse(self.device.posts())
        self.assert_safe_wire()

    async def test_challenge_403_is_bounded_without_auth_fallback(self):
        self.device.challenge_statuses = [403] * 3
        with self.assertRaises(CooldownError):
            await self.client().async_get_parameter("4")
        self.assertEqual(len(self.device.requests), 3)
        self.assertFalse(self.device.auths())
        self.assertEqual(self.sleep.await_count, 2)

    async def test_malformed_challenges_never_authenticate(self):
        for value in (None, {}, {"challenge": 1}, {"challenge": "bad"}):
            with self.subTest(value=value):
                self.device.challenge_document = value
                with self.assertRaises(ProtocolError):
                    await self.client().async_get_parameter("4")
        self.assertFalse(self.device.auths())

    async def test_auth_rejects_missing_cookie_without_fallback(self):
        self.device.issue_cookie = False
        with self.assertRaises(AuthenticationError):
            await self.client().async_get_parameter("4")
        self.assertEqual(len(self.device.auths()), 1)
        self.assertFalse(self.device.posts())
        self.assert_safe_wire()

    async def test_auth_200_with_cookie_is_accepted(self):
        self.device.auth_statuses = [200]
        with self.assertLogs("hvclient.client", level="WARNING") as logs:
            await self.client().async_get_parameter("4")
        self.assertNotIn(AUTH_COOKIE, str(logs.output))
        self.assert_safe_wire()

    async def test_redirect_is_never_followed(self):
        self.device.read_replies = [Reply(status=307, headers={"Location": "/factory-reset"})]
        with self.assertRaises(HTTPError) as caught:
            await self.client().async_get_parameter("4")
        self.assertEqual(caught.exception.status, 307)
        self.assertEqual(len(self.device.requests), 3)
        self.assert_safe_wire()

    async def test_http_reason_is_kept_but_hex_credentials_are_redacted(self):
        self.device.read_replies = [Reply(status=400, reason=f"bad parameter {AUTH_COOKIE}")]
        with self.assertRaises(HTTPError) as caught:
            await self.client().async_get_parameter("4")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("bad parameter", caught.exception.reason)
        self.assertNotIn(AUTH_COOKIE, str(caught.exception))

    async def test_json_and_size_errors_do_not_echo_response_bodies(self):
        self.device.read_replies = [Reply(raw=f"secret {AUTH_COOKIE}".encode())]
        with self.assertRaises(ProtocolError) as caught:
            await self.client().async_get_parameter("4")
        self.assertNotIn(AUTH_COOKIE, str(caught.exception))
        with self.assertRaisesRegex(ProtocolError, "max_response_bytes"):
            await self.client(max_response_bytes=100).async_get_parameter("4")

    async def test_malformed_catalogs_fail_closed(self):
        for document in ({}, {"parameters": [None]}, {"parameters": []},
                         {"parameters": [numeric(), numeric()]},
                         {"parameters": [numeric("other")]}):
            self.device.read_replies = [Reply(document=document)]
            with self.subTest(document=document), self.assertRaises(
                (ProtocolError, ParameterNotFoundError)
            ):
                await self.client().async_get_parameter("4")

    async def test_no_service_flag_or_raw_endpoint_can_escape_request_guard(self):
        client = self.client(write=True)
        for value in (None, False, 0, 1, "true"):
            body = {"key": KEY} if value is None else {"key": KEY, "daqOnly": value}
            with self.subTest(value=value), self.assertRaises(WritePolicyError):
                await client._request("POST", "/authenticate", body)
        for method, path, body in (
            ("POST", "/channels", None), ("POST", "/info", {}), ("PUT", "/pcb", {}),
            ("GET", "/factory-reset", None), ("POST", "/parameters?x", {}),
            ("PUT", "/parameters", {"parameters": [{"name": "4", "value": 21}]}),
            ("POST", "/parameters", {"parameters": [{"name": "E18", "value": True}]}),
            ("POST", "/parameters", {"parameters": [{"name": "4", "value": 21}] * 2}),
        ):
            with self.subTest(path=path, method=method), self.assertRaises(WritePolicyError):
                await client._request(method, path, body)
        self.assertFalse(self.device.requests)

    async def test_borrowed_session_cookie_override_middleware_and_ownership(self):
        jar = aiohttp.CookieJar(unsafe=True)
        jar.update_cookies({"auth-cookie": "f" * 64}, URL(f"http://127.0.0.1:{self.port}"))
        called = []

        async def middleware(request, handler):
            called.append(request)
            return await handler(request)

        async with aiohttp.ClientSession(cookie_jar=jar, middlewares=[middleware]) as session:
            client = self.client(session=session)
            await client.async_get_parameter("4")
            await client.async_get_parameter("5")
            await client.async_close()
            self.assertFalse(session.closed)
        self.assertFalse(called)
        parameter_requests = [r for r in self.device.requests if r["path"] == "/parameters"]
        self.assertEqual(parameter_requests[0]["cookie"], AUTH_COOKIE)
        self.assertEqual(parameter_requests[1]["cookie"], REFRESH_COOKIE)

    async def test_owned_session_close_and_closed_client_errors(self):
        client = self.client()
        self.assertIsNone(client._session)
        async with client:
            await client.async_get_parameter("4")
            session = client._session
        self.assertTrue(session.closed)
        await client.async_close()
        with self.assertRaises(ClientClosedError):
            await client.async_get_parameter("4")
        with self.assertRaises(ClientClosedError):
            await client.async_get_parameters([])

    async def test_parallel_reads_share_one_authentication(self):
        client = self.client()
        results = await asyncio.gather(*(client.async_get_parameter("4") for _ in range(10)))
        self.assertTrue(all(p.value == 20.5 for p in results))
        self.assertEqual(len(self.device.auths()), 1)

    async def test_read_waits_for_entire_write_readback_transaction(self):
        self.device.block_write = True
        client = self.client(write=True)
        write = asyncio.create_task(client.async_set_parameter("4", 21))
        await asyncio.wait_for(self.device.write_started.wait(), 1)
        read = asyncio.create_task(client.async_get_parameter("4"))
        await asyncio.sleep(0.01)
        self.assertFalse(read.done())
        count = len(self.device.requests)
        self.device.release_write.set()
        result, parameter = await asyncio.wait_for(asyncio.gather(write, read), 1)
        self.assertTrue(result.verified)
        self.assertEqual(parameter.value, 21)
        self.assertEqual([r["method"] for r in self.device.requests[count:]], ["GET", "GET"])

    async def test_cancellation_releases_lock_without_replaying_or_rollback(self):
        self.device.block_write = True
        client = self.client(write=True)
        write = asyncio.create_task(client.async_set_parameter("4", 21))
        await asyncio.wait_for(self.device.write_started.wait(), 1)
        write.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await write
        self.device.release_write.set()
        current = await asyncio.wait_for(client.async_get_parameter("4"), 1)
        self.assertEqual(current.value, 21)
        self.assertEqual(len(self.device.posts()), 1)
        self.assert_safe_wire()

    async def test_failed_connection_is_bounded_and_close_is_safe(self):
        # Stop only our own loopback listener; no external address is contacted.
        await self.site.stop()
        client = self.client(timeout=0.1, read_retries=1)
        with self.assertRaises(TransportError):
            await client.async_get_parameter("4")
        self.assertEqual(self.sleep.await_count, 1)
        await client.async_close()
        self.assertTrue(client._session.closed)

    async def test_auth_transport_has_no_public_mode_argument(self):
        with self.assertRaises(TypeError):
            AsyncHargassnerClient("127.0.0.1", daq_only=False)
        with self.assertRaises(TypeError):
            AsyncHargassnerClient("127.0.0.1", security_level="PLUMBER")

    async def test_readonly_snapshots_share_the_authenticated_client(self):
        client = self.client()
        channels = await client.async_get_channels()
        info = await client.async_get_info()
        pcb = await client.async_get_pcb()
        parameters = await client.async_get_parameters(["4"])
        self.assertIsInstance(channels, ChannelSnapshot)
        self.assertEqual(channels["example.temperature"], 42.5)
        self.assertIs(type(channels["example.state"]), int)
        self.assertIsInstance(info, SystemInfo)
        self.assertEqual(info.software_version, "test_firmware_1.0")
        self.assertEqual(info.system_id, "test-system-id")
        self.assertIsInstance(pcb, PcbInfo)
        self.assertEqual(pcb.internal_temperature, 26.5)
        self.assertEqual(pcb.internal_voltage, 23.8)
        self.assertEqual(parameters["4"].security_level, "USER")
        self.assertEqual(len(self.device.auths()), 1)
        self.assertFalse(self.device.posts())
        self.assertEqual([r["path"] for r in self.device.requests[2:]],
                         ["/channels", "/info", "/pcb", "/parameters"])
        self.assert_safe_wire()

    async def test_every_snapshot_refreshes_cookie_for_later_parameter_read(self):
        for path, method in (("/channels", "async_get_channels"), ("/info", "async_get_info"),
                             ("/pcb", "async_get_pcb")):
            with self.subTest(path=path):
                client = self.client()
                await getattr(client, method)()
                await client.async_get_parameter("4")
                self.assertEqual(self.device.requests[-2]["cookie"], AUTH_COOKIE)
                self.assertEqual(self.device.requests[-1]["cookie"], REFRESH_COOKIE)
                self.assertEqual(self.device.requests[-2]["path"], path)
        self.assert_safe_wire()

    async def test_each_snapshot_recovers_from_401_with_true_authentication(self):
        for path, method in (("/channels", "async_get_channels"), ("/info", "async_get_info"),
                             ("/pcb", "async_get_pcb")):
            with self.subTest(path=path):
                self.device.snapshot_replies[path] = [Reply(status=401)]
                auth_count = len(self.device.auths())
                await getattr(self.client(), method)()
                self.assertEqual(len(self.device.auths()) - auth_count, 2)
        self.assert_safe_wire()

    async def test_snapshot_401_recovery_is_bounded(self):
        for path, method in (("/channels", "async_get_channels"), ("/info", "async_get_info"),
                             ("/pcb", "async_get_pcb")):
            with self.subTest(path=path):
                self.device.snapshot_replies[path] = [Reply(status=401)] * 2
                auth_count = len(self.device.auths())
                with self.assertRaises(AuthenticationError):
                    await getattr(self.client(), method)()
                self.assertEqual(len(self.device.auths()) - auth_count, 2)
        self.assert_safe_wire()

    async def test_channels_404_never_uses_a_stronger_login_or_another_route(self):
        self.device.snapshot_replies["/channels"] = [Reply(status=404)]
        with self.assertRaises(HTTPError) as caught:
            await self.client().async_get_channels()
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.path, "/channels")
        self.assertEqual(len(self.device.auths()), 1)
        self.assertEqual(len(self.device.requests), 3)
        self.assert_safe_wire()

    async def test_snapshot_mutating_methods_bodies_and_suffixes_are_blocked(self):
        client = self.client(write=True)
        for path in ("/channels", "/info", "/pcb"):
            for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                with self.subTest(path=path, method=method), self.assertRaises(WritePolicyError):
                    await client._request(method, path, {})
            with self.subTest(path=path), self.assertRaises(WritePolicyError):
                await client._request("GET", path, {})
        for path in ("/info/systemid/generate", "/info/", "/pcb/calibrate", "/channels?x=1",
                     "/info%2fsystemid%2fgenerate", "/factory-reset"):
            with self.subTest(path=path), self.assertRaises(WritePolicyError):
                await client._request("GET", path)
        self.assertFalse(self.device.requests)

    async def test_snapshot_redirects_never_reach_identity_mutator(self):
        for path, method in (("/channels", "async_get_channels"), ("/info", "async_get_info"),
                             ("/pcb", "async_get_pcb")):
            with self.subTest(path=path):
                self.device.snapshot_replies[path] = [
                    Reply(status=307, headers={"Location": "/info/systemid/generate"})
                ]
                with self.assertRaises(HTTPError) as caught:
                    await getattr(self.client(), method)()
                self.assertEqual(caught.exception.status, 307)
        self.assert_safe_wire()

    async def test_invalid_snapshot_shapes_types_and_nonfinite_values_fail(self):
        cases = (
            ("/channels", "async_get_channels", []),
            ("/channels", "async_get_channels", {"channel": True}),
            ("/channels", "async_get_channels", {"channel": "21"}),
            ("/channels", "async_get_channels", {"channel": float("inf")}),
            ("/info", "async_get_info", None),
            ("/info", "async_get_info", {"softwareVersion": 340}),
            ("/pcb", "async_get_pcb", {}),
            ("/pcb", "async_get_pcb", {"internalTemperature": False, "internalVoltage": 24}),
        )
        for path, method, document in cases:
            with self.subTest(path=path, document=document):
                self.device.snapshot_replies[path] = [Reply(document=document)]
                with self.assertRaises(ProtocolError):
                    await getattr(self.client(), method)()
        self.assertFalse(self.device.posts())

    async def test_snapshot_response_size_limit_and_body_redaction(self):
        for path, method in (("/channels", "async_get_channels"), ("/info", "async_get_info"),
                             ("/pcb", "async_get_pcb")):
            with self.subTest(path=path):
                self.device.snapshot_replies[path] = [Reply(raw=b"private-device-id" * 20)]
                with self.assertRaisesRegex(ProtocolError, "max_response_bytes") as caught:
                    await getattr(self.client(max_response_bytes=100), method)()
                self.assertNotIn("private-device-id", str(caught.exception))
                self.device.snapshot_replies[path] = [Reply(raw=b"private-device-id")]
                with self.assertRaises(ProtocolError) as caught:
                    await getattr(self.client(), method)()
                self.assertNotIn("private-device-id", str(caught.exception))

    async def test_closed_client_cannot_read_snapshots(self):
        client = self.client()
        await client.async_close()
        for method in ("async_get_channels", "async_get_info", "async_get_pcb"):
            with self.subTest(method=method), self.assertRaises(ClientClosedError):
                await getattr(client, method)()
        self.assertFalse(self.device.requests)

    async def test_concurrent_mixed_reads_share_one_authentication(self):
        client = self.client()
        result = await asyncio.gather(
            client.async_get_channels(), client.async_get_info(), client.async_get_pcb(),
            client.async_get_parameter("4"), client.async_get_channels(),
        )
        self.assertEqual(result[0].to_dict(), result[4].to_dict())
        self.assertEqual(len(self.device.auths()), 1)
        self.assertFalse(self.device.posts())
        self.assert_safe_wire()

    async def test_snapshot_reads_wait_for_write_and_its_readback(self):
        self.device.block_write = True
        client = self.client(write=True)
        write = asyncio.create_task(client.async_set_parameter("4", 21))
        await asyncio.wait_for(self.device.write_started.wait(), 1)
        read = asyncio.create_task(client.async_get_channels())
        await asyncio.sleep(0.01)
        self.assertFalse(read.done())
        self.assertFalse(any(r["path"] == "/channels" for r in self.device.requests))
        self.device.release_write.set()
        written, channels = await asyncio.wait_for(asyncio.gather(write, read), 1)
        self.assertTrue(written.verified)
        self.assertEqual(channels.to_dict(), self.device.channels)
        self.assertEqual([r["path"] for r in self.device.requests[-2:]],
                         ["/parameters", "/channels"])
        self.assertEqual(len(self.device.posts()), 1)
        self.assert_safe_wire()

    async def test_snapshot_transient_read_retry_keeps_get_method_and_true_auth(self):
        for path, method in (("/channels", "async_get_channels"), ("/info", "async_get_info"),
                             ("/pcb", "async_get_pcb")):
            with self.subTest(path=path):
                client = self.client()
                original = client._request
                attempts = []

                async def transient(
                    request_method, request_path, body=None, *,
                    target=path, calls=attempts, send=original,
                ):
                    if request_path == target:
                        calls.append((request_method, body))
                        if len(calls) == 1:
                            raise TransportError("synthetic transient read failure")
                    return await send(request_method, request_path, body)

                with patch.object(client, "_request", side_effect=transient):
                    await getattr(client, method)()
                self.assertEqual(attempts, [("GET", None), ("GET", None)])
        self.assertFalse(self.device.posts())
        self.assert_safe_wire()
