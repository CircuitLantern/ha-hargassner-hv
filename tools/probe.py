#!/usr/bin/env python3
"""Read-only probe for the Hargassner Smart HV 2.7.4 HTTP API.

Defaults to ``daqOnly=true``, which makes only ``/channels`` reachable and does
not request SERVICE mode. To use the full API you must pass ``--full`` *and*
``--allow-service-mode``: a full login calls ``setRequestedMode(6)`` on the
boiler.

Examples::

    python3 tools/probe.py 192.0.2.10 challenge
    python3 tools/probe.py 192.0.2.10 channels
    python3 tools/probe.py 192.0.2.10 --full --allow-service-mode get /info
    python3 tools/probe.py 192.0.2.10 --full --allow-service-mode get /parameters
    python3 tools/probe.py 192.0.2.10 --full --allow-service-mode get /devices/rtc

This tool never sends POST/PUT/DELETE to anything except ``/authenticate``.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

SALT = "fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27"
COOKIE_RE = re.compile(r"auth-cookie=([0-9a-fA-F]{64})")
CHALLENGE_COOLDOWN_S = 5.0


@dataclass
class Result:
    status: int
    reason: str
    headers: dict[str, str]
    raw: bytes
    elapsed: float
    method: str
    path: str

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")

    def json(self) -> Any:
        if not self.raw:
            return None
        return json.loads(self.raw)


class ProbeError(Exception):
    pass


class Boiler:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.cookie: str | None = None
        self.daq_only: bool | None = None
        self._last_challenge = 0.0

    # -- transport ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float | None = None,
        retry: bool = False,
    ) -> Result:
        attempt = 0
        while True:
            try:
                return self._request_once(method, path, body, timeout)
            except (ConnectionError, OSError) as exc:
                if retry and attempt == 0:
                    attempt += 1
                    time.sleep(0.5)
                    continue
                raise ProbeError(f"{method} {path}: connection failed: {exc}") from exc

    def _request_once(
        self, method: str, path: str, body: Any, timeout: float | None
    ) -> Result:
        if timeout is None:
            timeout = self.timeout
        if method == "GET" and path.startswith("/devices/can"):
            timeout = max(timeout, 65.0)

        data: bytes | None
        headers = {
            "Accept": "application/json",
            "User-Agent": "hv-probe/0.1",
            "Connection": "close",
        }
        if body is None:
            data = None
        elif isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self.cookie:
            headers["Cookie"] = f"auth-cookie={self.cookie}"

        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        started = time.monotonic()
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            result = Result(
                status=resp.status,
                reason=resp.reason or "",
                headers={k.lower(): v for k, v in resp.getheaders()},
                raw=raw,
                elapsed=time.monotonic() - started,
                method=method,
                path=path,
            )
        finally:
            conn.close()

        set_cookie = result.headers.get("set-cookie")
        if set_cookie:
            match = COOKIE_RE.search(set_cookie)
            if match:
                self.cookie = match.group(1)
        return result

    # -- auth --------------------------------------------------------------

    def authenticate(self, daq_only: bool) -> None:
        last_error: str | None = None
        for attempt in range(3):
            self._respect_cooldown()
            challenge = self.request("GET", "/challenge", retry=True)
            self._last_challenge = time.monotonic()

            if challenge.status == 403:
                # Another client (or a previous call) owns the cooldown window.
                wait = CHALLENGE_COOLDOWN_S + 0.25
                print(
                    f"challenge rate-limited ({challenge.reason!r}); waiting {wait:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if challenge.status != 200:
                raise ProbeError(
                    f"GET /challenge: {challenge.status} {challenge.reason!r}"
                )

            try:
                challenge_hex = challenge.json()["challenge"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ProbeError(f"unexpected challenge body: {challenge.text!r}") from exc

            key = hashlib.sha256((challenge_hex + SALT).encode("ascii")).hexdigest()
            auth = self.request(
                "POST",
                "/authenticate",
                {"key": key, "daqOnly": daq_only},
                timeout=max(self.timeout, 15.0),
            )

            if auth.status == 204:
                if not self.cookie:
                    raise ProbeError(
                        "authentication returned 204 but no auth-cookie was set"
                    )
                self.daq_only = daq_only
                return

            last_error = f"{auth.status} {auth.reason!r}"
            if auth.status == 400 and attempt < 2:
                # The global challenge was probably overwritten by another client.
                print(
                    f"authentication failed ({last_error}); retrying handshake",
                    file=sys.stderr,
                )
                time.sleep(CHALLENGE_COOLDOWN_S + 0.25)
                continue
            raise ProbeError(f"POST /authenticate: {last_error}")

        raise ProbeError(f"authentication failed after retries: {last_error}")

    def _respect_cooldown(self) -> None:
        if not self._last_challenge:
            return
        remaining = CHALLENGE_COOLDOWN_S - (time.monotonic() - self._last_challenge)
        if remaining > 0:
            time.sleep(remaining + 0.05)


# -- output ---------------------------------------------------------------


def print_result(result: Result, *, raw: bool = False) -> None:
    print(f"{result.method} {result.path}")
    print(f"status: {result.status} {result.reason}")
    print(f"elapsed: {result.elapsed * 1000:.0f} ms")
    content_type = result.headers.get("content-type")
    if content_type:
        print(f"content-type: {content_type}")
    set_cookie = result.headers.get("set-cookie")
    if set_cookie:
        # Never print the cookie value.
        print("set-cookie: auth-cookie=<redacted>")
    print()
    if not result.raw:
        print("<empty body>")
        return
    if raw:
        print(result.text)
        return
    try:
        print(json.dumps(result.json(), indent=2, sort_keys=True))
    except ValueError:
        print(result.text)


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


# -- commands -------------------------------------------------------------


def cmd_challenge(args: argparse.Namespace, boiler: Boiler) -> int:
    """Exercise /challenge and show the 5 s cooldown without authenticating."""
    first = boiler.request("GET", "/challenge", retry=True)
    boiler._last_challenge = time.monotonic()
    print_result(first)
    print()
    second = boiler.request("GET", "/challenge", retry=True)
    print_result(second)
    if second.status == 403:
        print("\ncooldown confirmed: second challenge within 5 s was rejected")
    elif second.status == 200:
        print("\nWARNING: second challenge was accepted; cooldown may differ")
    return 0


def cmd_auth(args: argparse.Namespace, boiler: Boiler) -> int:
    started = time.monotonic()
    boiler.authenticate(daq_only=not args.full)
    elapsed = time.monotonic() - started
    mode = "full (SERVICE mode requested)" if args.full else "daq-only"
    print(f"authenticated in {elapsed:.2f}s; mode={mode}")
    print("session cookie acquired (value not shown)")
    return 0


def cmd_channels(args: argparse.Namespace, boiler: Boiler) -> int:
    boiler.authenticate(daq_only=True)
    result = boiler.request("GET", "/channels", retry=True)
    print_result(result, raw=args.raw)
    return 0 if result.status == 200 else 1


def cmd_get(args: argparse.Namespace, boiler: Boiler) -> int:
    if args.full and not args.allow_service_mode:
        print(
            "refusing full-mode login: pass --allow-service-mode to acknowledge "
            "that the boiler will be switched to SERVICE mode",
            file=sys.stderr,
        )
        return 2
    boiler.authenticate(daq_only=not args.full)
    result = boiler.request("GET", args.path, retry=True)
    print_result(result, raw=args.raw)
    return 0 if result.status == 200 else 1


# -- main -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only probe for the Smart HV HTTP API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Full mode requests SERVICE mode on the boiler. Use it deliberately.\n"
            "Default mode is daq-only, which exposes only /channels."
        ),
    )
    parser.add_argument("host", help="boiler IP or hostname")
    parser.add_argument("--port", type=int, default=80, help="HTTP port (default 80)")
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="request timeout in seconds"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="authenticate with daqOnly=false (full API; requests SERVICE mode)",
    )
    parser.add_argument(
        "--allow-service-mode",
        action="store_true",
        help="required with --full to confirm the SERVICE-mode side effect",
    )
    parser.add_argument("--raw", action="store_true", help="print bodies unformatted")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("challenge", help="test /challenge and the 5 s cooldown")

    sub.add_parser("auth", help="authenticate and report timing/mode")
    sub.add_parser("channels", help="authenticate daq-only and fetch /channels")

    get = sub.add_parser("get", help="authenticate and GET one path")
    get.add_argument("path", help="path beginning with /, e.g. /info")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.full and not args.allow_service_mode and args.command != "challenge":
        print(
            "warning: --full requests SERVICE mode; pass --allow-service-mode to proceed",
            file=sys.stderr,
        )
        return 2

    boiler = Boiler(args.host, args.port, args.timeout)
    commands = {
        "challenge": cmd_challenge,
        "auth": cmd_auth,
        "channels": cmd_channels,
        "get": cmd_get,
    }
    try:
        return commands[args.command](args, boiler)
    except ProbeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
