"""Offline wire-contract and policy tests; never connect to a real boiler."""

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from unittest.mock import patch

from tools import set_parameter as setter
from tools.probe import Boiler, ProbeError


PARAMETER = {
    "name": "4",
    "type": "FLOAT",
    "securityLevel": "USER",
    "active": True,
    "unit": "°C",
    "minimum": 0,
    "maximum": 30,
    "increment": 0.5,
    "value": 20,
}
CHALLENGE = "0" * 64
EXPECTED_KEY = (
    "a7cb08a85c66c00f574c4379c2cae8b2c5fe225ea87a4b9780edce4461282185"
    "aebd83a18eaeea52463076a3dd1bc0e19c9dc3d9d895c5debe3b2f480aeffd49"
)
COOKIE = "c" * 64


class Response:
    def __init__(self, value=None, *, status=200, headers=(), raw=None):
        self.status = status
        self.reason = "Mock response"
        self.headers = headers
        self.raw = json.dumps(value).encode() if raw is None else raw

    def read(self):
        return self.raw

    def getheaders(self):
        return self.headers


class Device:
    def __init__(self):
        self.parameter = copy.deepcopy(PARAMETER)
        self.requests = []
        self.challenges = []
        self.authentications = []
        self.reads = []
        self.write_response = None
        self.silent_rejection = False
        self.timeout_after_write = False
        self.auth_cookie = True

    def handle(self, request):
        method, path, body, headers = request
        if (method, path) == ("GET", "/challenge"):
            if self.challenges:
                return self.challenges.pop(0)
            return Response({"challenge": CHALLENGE})
        if (method, path) == ("POST", "/authenticate"):
            if self.authentications:
                return self.authentications.pop(0)
            cookie = [("Set-Cookie", f"auth-cookie={COOKIE}; Path=/")] if self.auth_cookie else []
            return Response(status=204, headers=cookie, raw=b"")
        if (method, path) == ("GET", "/parameters"):
            if self.reads:
                response = self.reads.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
            return Response({"parameters": [self.parameter]})
        if (method, path) == ("POST", "/parameters"):
            if not self.silent_rejection:
                self.parameter["value"] = body["parameters"][0]["value"]
            if self.timeout_after_write:
                raise TimeoutError("mock lost write response")
            return self.write_response or Response({"parameters": [self.parameter]})
        raise AssertionError(f"unexpected request {method} {path}")

    def connect(self, host, port, timeout):
        device = self

        class Connection:
            def request(self, method, path, body=None, headers=None):
                self.pending = (method, path, json.loads(body) if body else None, headers)
                device.requests.append(self.pending)

            def getresponse(self):
                return device.handle(self.pending)

            def close(self):
                pass

        return Connection()


class SetterTests(unittest.TestCase):
    def setUp(self):
        self.device = Device()
        self.addCleanup(patch.stopall)
        self.connect = patch(
            "tools.probe.http.client.HTTPConnection", side_effect=self.device.connect
        ).start()
        patch("tools.probe.time.sleep").start()
        patch("tools.probe.time.monotonic", return_value=100.0).start()
        self.client = setter.ParameterSetter("mock.invalid", 80, 10.0, apply=True)

    def run_change(self, value=20.5, expected=20):
        return setter.set_parameter(
            self.client, self.device.parameter["name"], value, expected=expected
        )

    def parameter_posts(self):
        return [r for r in self.device.requests if r[:2] == ("POST", "/parameters")]

    def assert_true_authentication_only(self):
        auth = [r for r in self.device.requests if r[:2] == ("POST", "/authenticate")]
        self.assertTrue(auth)
        for _, _, body, _ in auth:
            self.assertEqual(set(body), {"key", "daqOnly"})
            self.assertIs(body["daqOnly"], True)
            self.assertEqual(body["key"], EXPECTED_KEY)
        self.assertTrue(all(path in {"/challenge", "/authenticate", "/parameters"}
                            for _, path, _, _ in self.device.requests))

    def test_preview_has_no_parameter_write(self):
        self.client.apply = False
        report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertEqual(report["outcome"], "dry_run")
        self.assertEqual(report["before"], 20)
        self.assertEqual(self.parameter_posts(), [])
        self.assert_true_authentication_only()

    def test_success_sparse_wire_body_cookie_and_fresh_get(self):
        report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertEqual(report["outcome"], "verified")
        self.assertEqual(report["read_back"], Decimal("20.5"))
        posts = self.parameter_posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2], {"parameters": [{"name": "4", "value": 20.5}]})
        self.assertEqual(posts[0][3]["Cookie"], f"auth-cookie={COOKIE}")
        reads = [r for r in self.device.requests if r[:2] == ("GET", "/parameters")]
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(r[2] == {"parameters": ["4"]} for r in reads))
        self.assertEqual(self.device.requests[-1][:2], ("GET", "/parameters"))
        self.assert_true_authentication_only()

    def test_zero_is_a_value_not_a_missing_argument(self):
        report, code = self.run_change(0)
        self.assertEqual(code, 0)
        self.assertEqual(report["read_back"], 0)
        self.assertEqual(self.parameter_posts()[0][2]["parameters"][0]["value"], 0)

    def test_integer_negative_threshold(self):
        self.device.parameter.update(name="11", type="INTEGER", minimum=-20, value=0, increment=1)
        report, code = self.run_change(-5, expected=0)
        self.assertEqual(code, 0)
        self.assertEqual(report["read_back"], -5)
        self.assertIs(type(self.parameter_posts()[0][2]["parameters"][0]["value"]), int)

    def test_noop_does_not_post(self):
        report, code = self.run_change(20)
        self.assertEqual((report["outcome"], code), ("unchanged", 0))
        self.assertFalse(self.parameter_posts())

    def test_stale_expected_value_refuses(self):
        with self.assertRaisesRegex(setter.PolicyError, "does not match"):
            self.run_change(expected=19)
        self.assertFalse(self.parameter_posts())

    def test_apply_requires_expected_value_before_network(self):
        with self.assertRaisesRegex(setter.PolicyError, "requires --expect"):
            self.run_change(expected=None)
        self.connect.assert_not_called()

    def test_unallowlisted_names_fail_before_network(self):
        for name in ("E18", "Setup6", "system.factory_reset", "K38", "mode", "Hk1-2", "Hk1-2a"):
            with self.subTest(name=name), self.assertRaises(setter.PolicyError):
                setter.set_parameter(self.client, name, 1, expected=0)
        self.connect.assert_not_called()

    def test_inactive_or_changed_metadata_refuses_without_post(self):
        cases = [
            {"active": False}, {"active": None}, {"active": 1},
            {"securityLevel": "SERVICE"}, {"securityLevel": "DEVELOPER"},
            {"type": "BOOLEAN"}, {"type": "UNKNOWN_TYPE"}, {"unit": "F"},
            {"value": False}, {"value": "20"}, {"value": None},
            {"minimum": None}, {"maximum": "30"}, {"increment": False},
            {"minimum": 31}, {"increment": 0}, {"increment": -0.5},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(setter.PolicyError):
                setter.validate_change("4", dict(PARAMETER, **changes), Decimal("20.5"))
        for field in ("minimum", "maximum", "increment", "active", "securityLevel", "unit"):
            parameter = dict(PARAMETER)
            del parameter[field]
            with self.subTest(missing=field), self.assertRaises(setter.PolicyError):
                setter.validate_change("4", parameter, 20.5)
        self.connect.assert_not_called()

    def test_range_increment_type_and_finite_validation(self):
        for value in (-0.5, 30.5, 20.25, True, False, "20.5", {}, [], float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(setter.PolicyError):
                setter.validate_change("4", PARAMETER, value)
        integer = dict(PARAMETER, name="2", type="INTEGER", maximum=75, increment=1)
        with self.assertRaises(setter.PolicyError):
            setter.validate_change("2", integer, Decimal("60.0"))
        self.assertEqual(setter.validate_change("2", integer, 0), 0)

    def test_inactive_live_setting_is_not_overridable(self):
        self.device.parameter["active"] = False
        with self.assertRaisesRegex(setter.PolicyError, "inactive"):
            self.run_change()
        self.assertFalse(self.parameter_posts())

    def test_extremely_large_finite_input_fails_without_post(self):
        with self.assertRaisesRegex(setter.PolicyError, "outside the live range"):
            self.run_change(Decimal("1e9999"))
        self.assertFalse(self.parameter_posts())

    def test_step_is_decimal_and_anchored_at_minimum(self):
        parameter = dict(PARAMETER, minimum=0.1, maximum=3.1, increment=0.2)
        self.assertEqual(setter.validate_change("4", parameter, Decimal("0.3")), 0.3)
        with self.assertRaises(setter.PolicyError):
            setter.validate_change("4", parameter, Decimal("0.2"))
        with self.assertRaises(setter.PolicyError):
            setter.validate_change("4", PARAMETER, Decimal("20.50000000000000001"))

    def test_silent_200_rejection_is_failure(self):
        self.device.silent_rejection = True
        report, code = self.run_change()
        self.assertEqual(code, 1)
        self.assertEqual(report["outcome"], "unverified")
        self.assertFalse(report["read_back_matches"])
        self.assertEqual(report["read_back"], 20)
        self.assertEqual(len(self.parameter_posts()), 1)

    def test_200_post_success_but_get_mismatch_is_failure(self):
        before = Response({"parameters": [PARAMETER]})
        self.device.reads = [before, before]
        report, code = self.run_change()
        self.assertEqual(code, 1)
        self.assertFalse(report["read_back_matches"])
        self.assertEqual(len(self.parameter_posts()), 1)

    def test_lost_write_response_reconciles_but_never_replays(self):
        self.device.timeout_after_write = True
        report, code = self.run_change()
        self.assertEqual((report["outcome"], code), ("unverified", 1))
        self.assertTrue(report["read_back_matches"])
        self.assertIn("mock lost write response", report["write_error"])
        self.assertEqual(len(self.parameter_posts()), 1)
        self.assert_true_authentication_only()

    def test_missing_readback_does_not_claim_success(self):
        self.device.reads = [Response({"parameters": [PARAMETER]}), Response(raw=b"not JSON")]
        report, code = self.run_change()
        self.assertEqual((report["outcome"], code), ("unverified", 1))
        self.assertIn("read_back_error", report)
        self.assertEqual(len(self.parameter_posts()), 1)

    def test_post_401_never_reauthenticates_to_replay(self):
        self.device.write_response = Response(status=401)
        report, code = self.run_change()
        self.assertEqual(code, 1)
        self.assertEqual(report["write_status"], 401)
        self.assertEqual(len(self.parameter_posts()), 1)
        self.assertEqual(sum(r[:2] == ("POST", "/authenticate") for r in self.device.requests), 1)

    def test_malformed_post_response_still_reads_back(self):
        self.device.write_response = Response(raw=b"garbage")
        report, code = self.run_change()
        self.assertEqual(code, 1)
        self.assertTrue(report["read_back_matches"])
        self.assertIn("invalid JSON", report["write_error"])

    def test_non_numeric_post_value_is_not_echoed_in_report(self):
        self.device.write_response = Response({
            "parameters": [{"name": "4", "value": {"unexpected": COOKIE}}]
        })
        report, code = self.run_change()
        self.assertEqual(code, 1)
        self.assertNotIn("returned", report)
        self.assertNotIn(COOKIE, str(report))

    def test_http_rejection_still_reconciles_without_replay(self):
        self.device.write_response = Response(status=400)
        report, code = self.run_change()
        self.assertEqual(code, 1)
        self.assertEqual(report["write_status"], 400)
        self.assertIn("read_back", report)
        self.assertEqual(len(self.parameter_posts()), 1)

    def test_lost_write_and_readback_responses_are_unknown(self):
        self.device.timeout_after_write = True
        self.device.reads = [
            Response({"parameters": [PARAMETER]}),
            TimeoutError("mock read failed"), TimeoutError("mock read failed"),
        ]
        report, code = self.run_change()
        self.assertEqual((report["outcome"], code), ("unverified", 1))
        self.assertIn("write_error", report)
        self.assertIn("read_back_error", report)
        self.assertNotIn("read_back_matches", report)
        self.assertEqual(len(self.parameter_posts()), 1)

    def test_read_401_recovers_with_true_only(self):
        self.device.reads = [Response(status=401)]
        report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertEqual(sum(r[:2] == ("POST", "/authenticate") for r in self.device.requests), 2)
        self.assert_true_authentication_only()

    def test_readback_401_recovers_without_replaying_post(self):
        self.device.reads = [Response({"parameters": [PARAMETER]}), Response(status=401)]
        report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.parameter_posts()), 1)
        self.assert_true_authentication_only()

    def test_read_connection_retry_does_not_duplicate_write(self):
        self.device.reads = [TimeoutError("mock transient read failure")]
        report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.parameter_posts()), 1)
        self.assert_true_authentication_only()

    def test_repeated_read_401_is_bounded_and_never_uses_service(self):
        self.device.reads = [Response(status=401), Response(status=401)]
        with self.assertRaises(ProbeError):
            self.run_change()
        self.assertFalse(self.parameter_posts())
        self.assertEqual(sum(r[:2] == ("POST", "/authenticate") for r in self.device.requests), 2)
        self.assert_true_authentication_only()

    def test_auth_handshake_retry_stays_true(self):
        self.device.authentications = [Response(status=400)]
        with redirect_stderr(io.StringIO()):
            report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.parameter_posts()), 1)
        self.assert_true_authentication_only()

    def test_challenge_cooldown_does_not_weaken_auth(self):
        self.device.challenges = [Response(status=403)]
        with redirect_stderr(io.StringIO()):
            report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assert_true_authentication_only()

    def test_failed_auth_has_no_parameter_post_or_fallback(self):
        self.device.authentications = [Response(status=403)]
        with self.assertRaises(ProbeError):
            self.run_change()
        self.assertFalse(self.parameter_posts())
        self.assert_true_authentication_only()

    def test_missing_cookie_fails_without_fallback(self):
        self.device.auth_cookie = False
        with self.assertRaises(ProbeError):
            self.run_change()
        self.assertFalse(self.parameter_posts())
        self.assert_true_authentication_only()

    def test_auth_200_with_cookie_is_accepted_without_mode_fallback(self):
        self.device.authentications = [Response(
            status=200, headers=[("Set-Cookie", f"auth-cookie={COOKIE}; Path=/")]
        )]
        with redirect_stderr(io.StringIO()) as err:
            report, code = self.run_change()
        self.assertEqual(code, 0)
        self.assertIn("returned 200", err.getvalue())
        self.assertNotIn(COOKIE, err.getvalue())
        self.assert_true_authentication_only()

    def test_redirect_is_not_followed(self):
        self.device.authentications = [
            Response(status=302, headers=[("Location", "http://other.invalid/")])
        ]
        with self.assertRaises(ProbeError):
            self.run_change()
        self.assertEqual(len(self.device.requests), 2)
        self.assertFalse(self.parameter_posts())
        self.assert_true_authentication_only()

    def test_malformed_challenge_never_authenticates(self):
        for value in (None, {}, {"challenge": 123}, {"challenge": "bad"}):
            with self.subTest(value=value):
                self.device.challenges = [Response(value)]
                with self.assertRaises(ProbeError):
                    self.client.authenticate()
        self.assertFalse(any(r[:2] == ("POST", "/authenticate") for r in self.device.requests))

    def test_false_auth_blocked_even_if_base_auth_is_called(self):
        for value in (False, None, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaises(setter.PolicyError):
                self.client.authenticate(value)
        self.connect.assert_not_called()
        with patch("tools.probe.warn_service_mode"), self.assertRaises(setter.PolicyError):
            Boiler.authenticate(self.client, daq_only=False)
        self.assertFalse(any(r[:2] == ("POST", "/authenticate") for r in self.device.requests))

    def test_request_boundary_rejects_missing_or_false_daq_flag(self):
        for body in ({"key": EXPECTED_KEY}, {"key": EXPECTED_KEY, "daqOnly": False},
                     {"key": EXPECTED_KEY, "daqOnly": 1}, {"key": "bad", "daqOnly": True}):
            with self.subTest(body=body), self.assertRaises(setter.PolicyError):
                self.client.request("POST", "/authenticate", body)
        self.connect.assert_not_called()

    def test_request_boundary_denies_raw_paths_batches_and_metadata(self):
        cases = [
            ("GET", "/factory-reset", None),
            ("POST", "/info", {}),
            ("PUT", "/parameters", {"parameters": [{"name": "4", "value": 20}]}),
            ("POST", "/parameters?extra", {}),
            ("GET", "/parameters", None),
            ("POST", "/parameters", {"parameters": [{"name": "E18", "value": True}]}),
            ("POST", "/parameters", {"parameters": [{"name": "4", "value": 20}] * 2}),
            ("POST", "/parameters", {
                "parameters": [{"name": "4", "value": 20, "last_changed": "x"}]
            }),
        ]
        for method, path, body in cases:
            with self.subTest(method=method, path=path), self.assertRaises(setter.PolicyError):
                self.client.request(method, path, body)
        self.connect.assert_not_called()

    def test_preview_and_no_session_cannot_write(self):
        body = {"parameters": [{"name": "4", "value": 20.5}]}
        with self.assertRaises(setter.PolicyError):
            self.client.request("POST", "/parameters", body)
        self.client.authenticate()
        self.client.apply = False
        with self.assertRaises(setter.PolicyError):
            self.client.request("POST", "/parameters", body)
        self.assertFalse(self.parameter_posts())

    def test_write_retry_and_second_post_forbidden_at_transport_boundary(self):
        self.client.authenticate()
        body = {"parameters": [{"name": "4", "value": 20.5}]}
        with self.assertRaises(setter.PolicyError):
            self.client.request("POST", "/parameters", body, retry=True)
        self.client.request("POST", "/parameters", body)
        with self.assertRaises(setter.PolicyError):
            self.client.request("POST", "/parameters", body)
        self.assertEqual(len(self.parameter_posts()), 1)

    def test_bad_parameter_envelopes_fail_closed(self):
        for value in ({}, {"parameters": []}, {"parameters": [PARAMETER, PARAMETER]},
                      {"parameters": [dict(PARAMETER, name="other")]}, {"parameters": [None]}):
            with self.subTest(value=value), self.assertRaises(ProbeError):
                setter.parameter_object(response_result(value), "4")

    def test_nonfinite_json_response_fails_before_write(self):
        self.device.reads = [Response(raw=b'{"parameters":[{"name":"4","value":NaN}]}')]
        with self.assertRaisesRegex(ProbeError, "invalid JSON"):
            self.run_change()
        self.assertFalse(self.parameter_posts())

    def test_cli_preview_and_apply_output_redact_auth(self):
        for flags, outcome in (([], "dry_run"), (["--apply", "--expect", "20"], "verified")):
            with self.subTest(flags=flags):
                with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    code = setter.main(["mock.invalid", "4", "20.5", *flags])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["outcome"], outcome)
            for secret in (COOKIE, EXPECTED_KEY, CHALLENGE):
                self.assertNotIn(secret, out.getvalue() + err.getvalue())

    def test_cli_input_errors_cannot_connect(self):
        bad_args = [
            ["mock.invalid", "4", "true"], ["mock.invalid", "4", "NaN"],
            ["mock.invalid", "4", "Infinity"],
            ["mock.invalid", "4", "20.5", "--apply"],
            ["mock.invalid", "4", "20.5", "--full"],
            ["mock.invalid", "4", "20.5", "--allow-service-mode"],
            ["http://mock.invalid", "4", "20.5"],
            ["mock.invalid", "4", "20.5", "--timeout", "nan"],
            ["mock.invalid", "4", "20.5", "--port", "0"],
        ]
        for args in bad_args:
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    setter.main(args)
                self.assertEqual(exc.exception.code, 2)
        self.connect.assert_not_called()

    def test_cli_rejected_write_has_nonzero_exit(self):
        self.device.silent_rejection = True
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = setter.main(["mock.invalid", "4", "20.5", "--apply", "--expect", "20"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["outcome"], "unverified")
        self.assertIn("do not blindly repeat", err.getvalue())

    def test_cli_stale_value_has_refusal_exit_and_no_write(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = setter.main(["mock.invalid", "4", "20.5", "--apply", "--expect", "19"])
        self.assertEqual(code, 2)
        self.assertIn("no write sent", err.getvalue())
        self.assertFalse(self.parameter_posts())

    def test_interrupt_after_write_warns_of_uncertain_outcome(self):
        handle = self.device.handle

        def interrupt(request):
            if request[:2] == ("POST", "/parameters"):
                raise KeyboardInterrupt
            return handle(request)

        self.device.handle = interrupt
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = setter.main(["mock.invalid", "4", "20.5", "--apply", "--expect", "20"])
        self.assertEqual(code, 130)
        self.assertIn("outcome may be unknown", err.getvalue())
        self.assertEqual(len(self.parameter_posts()), 1)


def response_result(value):
    # Build the same Result that the real probe transport hands the setter.
    return setter.Result(200, "OK", {}, json.dumps(value).encode(), 0, "GET", "/parameters")


if __name__ == "__main__":
    unittest.main()
