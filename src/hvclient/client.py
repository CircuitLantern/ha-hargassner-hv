"""Guarded async HTTP client. Authentication never requests SERVICE mode."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from typing import Any, Self

import aiohttp
from yarl import URL

from .catalog import supported_parameter
from .curves import HEATING_CURVES
from .errors import (
    AuthenticationError,
    ClientClosedError,
    CooldownError,
    HargassnerError,
    HTTPError,
    ParameterAccessError,
    ParameterNotFoundError,
    ProtocolError,
    TransportError,
    ValidationError,
    WriteMismatchError,
    WriteOutcomeUnknownError,
    WritePolicyError,
)
from .models import (
    ChannelSnapshot,
    Parameter,
    PcbInfo,
    SystemInfo,
    WritePreview,
    WriteResult,
    channels_from_json,
    parameter_from_json,
    pcb_info_from_json,
    system_info_from_json,
)
from .modes import CIRCUIT_MODES, mode_id
from .policy import (
    WritePolicy,
    numeric_input,
    parameter_input,
    stored_value,
    validate_change,
    validate_curve_context,
    validate_mode_context,
    validate_mode_parameter,
    validate_schedule_context,
    validate_schedule_parameter,
    values_equal,
    wire_value,
)
from .schedules import SCHEDULE_PARAMETERS, normalize_schedule

_LOGGER = logging.getLogger(__name__)
_SALT = "fa21228be4b513b2c28d6841f520a73e87cdee5fe8fdfd87201330084a033f27"
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_CHALLENGE = re.compile(r"[0-9a-f]{64}")
_COOLDOWN = 5.25
_SNAPSHOT_PATHS = frozenset({"/channels", "/info", "/pcb"})
_READ_PATHS = _SNAPSHOT_PATHS | {"/parameters"}
_UNSET = object()
_sleep = asyncio.sleep


@dataclass(frozen=True)
class _Response:
    status: int
    reason: str
    body: bytes = field(repr=False)
    cookie: str | None = field(default=None, repr=False)

    def json(self) -> Any:
        def reject_constant(value: str) -> None:
            raise ValueError("non-finite JSON constant")

        try:
            return json.loads(self.body, parse_constant=reject_constant)
        except (ValueError, RecursionError) as exc:
            raise ProtocolError("invalid JSON response (body omitted)") from exc


def _name(value: Any) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValidationError("expected a nonempty API parameter name, at most 256 characters")
    return value


def _names(values: Iterable[str] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, (str, bytes)):
        raise ValidationError("names must be a collection; use async_get_parameter for one name")
    selected = []
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValidationError("names must be a collection of API names") from exc
    for value in iterator:
        selected.append(_name(value))
        if len(selected) > 256:
            raise ValidationError("at most 256 parameter names may be selected")
    if len(set(selected)) != len(selected):
        raise ValidationError("duplicate parameter names in selection")
    return tuple(selected)


def _cookie(headers: Iterable[str]) -> str | None:
    token = None
    for header in headers:
        cookies = SimpleCookie()
        try:
            cookies.load(header)
        except CookieError:
            continue
        morsel = cookies.get("auth-cookie")
        if (
            morsel is not None and morsel["max-age"] != "0"
            and _HEX64.fullmatch(morsel.value)
        ):
            token = morsel.value
    return token


class AsyncHargassnerClient:
    """Telemetry, USER parameters and reviewed curve/circuit-mode exceptions.

    No I/O occurs until an async read/write operation is invoked.

    One operation lock serializes authentication, reads, and each complete
    write/read-back transaction. Internal helpers never re-acquire that lock.
    The instance coordinates only its own callers, not other clients/devices.
    """

    def __init__(
        self,
        host: str,
        port: int = 80,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 10.0,
        write_policy: WritePolicy | None = None,
        read_retries: int = 1,
        authentication_attempts: int = 3,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if not isinstance(host, str) or not host or any(c in host for c in "/?#@\\ \t\r\n"):
            raise ValueError("host must be a bare IP address or hostname, not a URL")
        if ":" in host:
            try:
                ipaddress.IPv6Address(host)
            except ValueError as exc:
                raise ValueError("specify the port separately; IPv6 hosts must be unbracketed") from exc
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be 1..65535")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(read_retries) is not int or not 0 <= read_retries <= 3:
            raise ValueError("read_retries must be 0..3")
        if type(authentication_attempts) is not int or not 1 <= authentication_attempts <= 3:
            raise ValueError("authentication_attempts must be 1..3")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 8 * 1024 * 1024:
            raise ValueError("max_response_bytes must be 1..8388608")
        if write_policy is not None and not isinstance(write_policy, WritePolicy):
            raise TypeError("write_policy must be a WritePolicy")
        self._base_url = URL.build(scheme="http", host=host, port=port)
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._write_policy = write_policy if write_policy is not None else WritePolicy()
        self._read_retries = read_retries
        self._authentication_attempts = authentication_attempts
        self._max_response_bytes = max_response_bytes
        self._operation_lock = asyncio.Lock()
        self._closed = False
        self._cookie: str | None = None
        self._last_challenge_at: float | None = None

    async def __aenter__(self) -> Self:
        self._check_open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.async_close()

    def _check_open(self) -> None:
        if self._closed:
            raise ClientClosedError("client is closed")

    async def async_close(self) -> None:
        """Close an owned session, never a borrowed one; no mode/HTTP requests."""
        async with self._operation_lock:
            self._closed = True
            self._cookie = None
            if self._owns_session and self._session is not None:
                await self._session.close()

    def _guard_request(self, method: str, path: str, body: Any) -> None:
        if (method, path) == ("GET", "/challenge") and body is None:
            return
        if method == "GET" and path in _SNAPSHOT_PATHS and body is None:
            return
        if (method, path) == ("POST", "/authenticate"):
            if (
                isinstance(body, dict) and set(body) == {"key", "daqOnly"}
                and body["daqOnly"] is True and isinstance(body["key"], str)
                and re.fullmatch(r"[0-9a-f]{128}", body["key"])
            ):
                return
            raise WritePolicyError("authentication requires explicit daqOnly=true")
        if path == "/parameters" and method == "GET":
            if body is None:
                return
            if (
                isinstance(body, dict) and set(body) == {"parameters"}
                and isinstance(body["parameters"], list)
            ):
                _names(body["parameters"])
                return
        if path == "/parameters" and method == "POST":
            entries = body.get("parameters") if isinstance(body, dict) else None
            if (
                isinstance(body, dict) and set(body) == {"parameters"}
                and isinstance(entries, list) and len(entries) == 1
                and isinstance(entries[0], dict) and set(entries[0]) == {"name", "value"}
            ):
                name = _name(entries[0]["name"])
                self._write_policy.require(name)
                value = entries[0]["value"]
                if name in SCHEDULE_PARAMETERS:
                    canonical = normalize_schedule(name, value).to_json()
                    if value != canonical:
                        raise WritePolicyError("circuit-schedule wire value must be canonical")
                elif name in CIRCUIT_MODES:
                    if not isinstance(value, dict) or set(value) != {"id"}:
                        raise WritePolicyError("circuit-mode wire value must contain only id")
                    mode_id(value["id"])
                else:
                    numeric_input(name, value)
                if self._cookie is not None:
                    return
        raise WritePolicyError("request is outside the supported endpoint/method policy")

    async def _request(self, method: str, path: str, body: Any = None) -> _Response:
        """Exactly one transport attempt; no redirect, middleware, or POST retry."""
        self._check_open()
        self._guard_request(method, path, body)
        if self._session is None:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(), trust_env=False
            )
        if self._session.closed:
            raise ClientClosedError("HTTP session is closed")
        headers = {"Accept": "application/json", "User-Agent": "hargassner-hvclient/0.1"}
        data = None
        if body is not None:
            # Do not delegate serialization to a borrowed session's serializer.
            data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        # Per-request cookies override an unsafe/shared session jar's stale token.
        cookies = {"auth-cookie": self._cookie} if self._cookie is not None else {}
        try:
            async with self._session.request(
                method, self._base_url.with_path(path), data=data, headers=headers,
                cookies=cookies, timeout=self._timeout, allow_redirects=False,
                middlewares=(),
            ) as response:
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    if len(chunks) + len(chunk) > self._max_response_bytes:
                        raise ProtocolError("response exceeds max_response_bytes")
                    chunks.extend(chunk)
                result = _Response(
                    response.status, response.reason or "", bytes(chunks),
                    _cookie(response.headers.getall("Set-Cookie", [])),
                )
        except (aiohttp.ClientError, OSError) as exc:
            # No underlying exception text/body/header logging (may hold secrets).
            raise TransportError(f"{method} {path}: connection failed or timed out") from exc
        if path in _READ_PATHS and result.status == 200 and result.cookie is not None:
            self._cookie = result.cookie
        return result

    async def _read_response(self, path: str, body: Any = None) -> _Response:
        for attempt in range(self._read_retries + 1):
            try:
                return await self._request("GET", path, body)
            except TransportError:
                if attempt == self._read_retries:
                    raise
                await _sleep(0.25)
        raise AssertionError("unreachable")

    async def _authenticate(self) -> None:
        self._cookie = None
        for attempt in range(self._authentication_attempts):
            if self._last_challenge_at is not None:
                remaining = _COOLDOWN - (time.monotonic() - self._last_challenge_at)
                if remaining > 0:
                    await _sleep(remaining)
            challenge = await self._read_response("/challenge")
            self._last_challenge_at = time.monotonic()
            if challenge.status == 403:
                if attempt + 1 == self._authentication_attempts:
                    raise CooldownError("challenge cooldown retry budget exhausted")
                continue
            if challenge.status != 200:
                raise HTTPError("GET", "/challenge", challenge.status, challenge.reason)
            document = challenge.json()
            value = document.get("challenge") if isinstance(document, dict) else None
            if not isinstance(value, str) or _CHALLENGE.fullmatch(value) is None:
                raise ProtocolError("expected a 64-character lowercase hex challenge")
            key = hashlib.sha512((value + _SALT).encode("ascii")).hexdigest()
            # No flag argument, mode option, missing-flag path, or stronger login.
            auth = await self._request("POST", "/authenticate", {"key": key, "daqOnly": True})
            if auth.status in (200, 204) and auth.cookie is not None:
                self._cookie = auth.cookie
                if auth.status == 200:
                    _LOGGER.warning("Authentication returned 200 rather than 204; cookie issued")
                return
            if auth.status == 400 and attempt + 1 < self._authentication_attempts:
                continue  # Another client may have replaced the global challenge.
            if auth.status not in (200, 204):
                raise HTTPError("POST", "/authenticate", auth.status, auth.reason)
            raise AuthenticationError("authentication did not issue a valid auth-cookie")
        raise AuthenticationError("authentication retry budget exhausted")

    async def _authenticated_read(self, path: str, body: Any = None) -> _Response:
        """Shared, bounded read recovery; never changes the authentication mode."""
        self._guard_request("GET", path, body)
        if self._cookie is None:
            await self._authenticate()
        response = await self._read_response(path, body)
        if response.status == 401:
            await self._authenticate()
            response = await self._read_response(path, body)
        if response.status == 401:
            self._cookie = None
            raise AuthenticationError(f"GET {path}: still unauthorized after one re-authentication")
        if response.status != 200:
            # In particular, /channels 404 is not a reason for a stronger login.
            raise HTTPError("GET", path, response.status, response.reason)
        return response

    async def _get_parameters(self, names: tuple[str, ...] | None) -> dict[str, Parameter]:
        body = None if names is None else {"parameters": list(names)}
        response = await self._authenticated_read("/parameters", body)
        return self._parameters(response, names)

    @staticmethod
    def _parameter_rows(
        response: _Response, names: tuple[str, ...] | None
    ) -> dict[str, dict[str, Any]]:
        document = response.json()
        rows = document.get("parameters") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise ProtocolError("response has no parameters array")
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise ProtocolError("invalid parameter catalog entry")
            name = row["name"]
            if name in result:
                raise ProtocolError("duplicate parameter API name in response")
            if names is not None and name not in names:
                raise ProtocolError("selected read returned an unrequested parameter")
            result[name] = row
        return result

    @staticmethod
    def _parameters(response: _Response, names: tuple[str, ...] | None) -> dict[str, Parameter]:
        parameters = {}
        for name, row in AsyncHargassnerClient._parameter_rows(response, names).items():
            if not supported_parameter(name, row.get("securityLevel")):
                if names is not None:
                    raise ParameterAccessError("selected parameter is outside the supported catalog")
                continue
            try:
                parameters[name] = parameter_from_json(row)
            except RecursionError as exc:
                raise ProtocolError("parameter JSON nesting exceeds the supported depth") from exc
        if names is not None and set(names) != set(parameters):
            raise ParameterNotFoundError("one or more selected parameters were not returned")
        return parameters

    async def async_get_parameters(
        self, names: Iterable[str] | None = None
    ) -> dict[str, Parameter]:
        """Read USER parameters plus six curve and three circuit-mode exceptions.

        Include inactive supported entries. Other non-USER entries are discarded
        from the full catalog and refused when selected. No general level option.
        """
        selected = _names(names)
        async with self._operation_lock:
            self._check_open()
            if selected == ():
                return {}
            return await self._get_parameters(selected)

    async def async_get_parameter(self, name: str) -> Parameter:
        name = _name(name)
        return (await self.async_get_parameters((name,)))[name]

    async def async_get_channels(self) -> ChannelSnapshot:
        """Fetch all currently registered channels; numeric snapshot, not a setter."""
        async with self._operation_lock:
            self._check_open()
            response = await self._authenticated_read("/channels")
            return channels_from_json(response.json())

    async def async_get_info(self) -> SystemInfo:
        """Read firmware/device identity; no identity-write/generation operation."""
        async with self._operation_lock:
            self._check_open()
            response = await self._authenticated_read("/info")
            return system_info_from_json(response.json())

    async def async_get_pcb(self) -> PcbInfo:
        """Read board temperature/voltage; no hardware control or calibration."""
        async with self._operation_lock:
            self._check_open()
            response = await self._authenticated_read("/pcb")
            return pcb_info_from_json(response.json())

    async def _preview(self, name: str, value: Any, expected: Any) -> WritePreview:
        circuit_type = None
        if name in SCHEDULE_PARAMETERS:
            parameter, circuit_type = await self._get_schedule_context(name)
        elif name in CIRCUIT_MODES:
            parameter, circuit_type = await self._get_mode_context(name)
        elif name in HEATING_CURVES:
            parameter = await self._get_curve_context(name)
        else:
            parameter = (await self._get_parameters((name,)))[name]
        requested = validate_change(parameter, value, self._write_policy)
        if expected is not _UNSET:
            expected_value = mode_id(expected) if name in CIRCUIT_MODES else expected
            if not values_equal(name, expected_value, stored_value(parameter)):
                raise ValidationError("current value does not match expected; no update sent")
        return WritePreview(parameter, requested, circuit_type)

    async def _get_schedule_context(
        self, name: str, *, expected_type: int | None = None
    ) -> tuple[Parameter, int]:
        program = SCHEDULE_PARAMETERS[name]
        names = (name, program.peer, program.selector, "D9")
        response = await self._authenticated_read("/parameters", {"parameters": list(names)})
        rows = self._parameter_rows(response, names)
        if set(rows) != set(names):
            raise ParameterNotFoundError("circuit-schedule configuration context was not returned")
        try:
            parameter = parameter_from_json(rows[name])
            peer = parameter_from_json(rows[program.peer])
        except RecursionError as exc:
            raise ProtocolError("circuit-schedule JSON nesting exceeds the supported depth") from exc
        configured = validate_schedule_context(
            parameter, peer, rows[program.selector], rows["D9"], expected_type
        )
        return parameter, configured

    async def _get_mode_context(
        self, name: str, *, expected_type: int | None = None
    ) -> tuple[Parameter, int]:
        selector = CIRCUIT_MODES[name]
        names = (name, selector)
        response = await self._authenticated_read("/parameters", {"parameters": list(names)})
        rows = self._parameter_rows(response, names)
        if set(rows) != set(names):
            raise ParameterNotFoundError("circuit-mode configuration context was not returned")
        try:
            parameter = parameter_from_json(rows[name])
        except RecursionError as exc:
            raise ProtocolError("circuit-mode JSON nesting exceeds the supported depth") from exc
        configured = validate_mode_context(parameter, rows[selector], expected_type)
        return parameter, configured

    async def _get_curve_context(self, name: str) -> Parameter:
        profile = HEATING_CURVES[name]
        names = (name, profile.peer, profile.selector)
        response = await self._authenticated_read("/parameters", {"parameters": list(names)})
        rows = self._parameter_rows(response, names)
        if set(rows) != set(names):
            raise ParameterNotFoundError("heating-curve profile context was not returned")
        try:
            parameter = parameter_from_json(rows[name])
            peer = parameter_from_json(rows[profile.peer])
        except RecursionError as exc:
            raise ProtocolError("heating-curve JSON nesting exceeds the supported depth") from exc
        # Selector data are internal validation context, not public catalog access.
        validate_curve_context(parameter, peer, rows[profile.selector])
        return parameter

    async def async_preview_parameter_write(
        self, name: str, value: Any, *, expected: Any = _UNSET
    ) -> WritePreview:
        """Fetch and validate a proposed write, with no parameter POST."""
        name = _name(name)
        self._write_policy.require(name)
        value = parameter_input(name, value)
        if name in SCHEDULE_PARAMETERS and expected is not _UNSET:
            expected = normalize_schedule(name, expected, replacement=False)
        async with self._operation_lock:
            self._check_open()
            return await self._preview(name, value, expected)

    async def async_set_parameter(
        self, name: str, value: Any, *, expected: Any = _UNSET
    ) -> WriteResult:
        """One opt-in sparse update followed by read-back; never retry the POST.

        Cancellation propagates without replay/rollback. If cancelled after
        transmission the value may have changed; explicitly inspect it next.
        """
        name = _name(name)
        self._write_policy.require(name)
        value = parameter_input(name, value)
        if name in HEATING_CURVES and expected is _UNSET:
            raise ValidationError("heating-curve writes require an expected current slope")
        if name in CIRCUIT_MODES:
            if expected is _UNSET:
                raise ValidationError("circuit-mode writes require an expected current mode ID")
            mode_id(expected)
        if name in SCHEDULE_PARAMETERS:
            if expected is _UNSET and SCHEDULE_PARAMETERS[name].limits.expected_required:
                raise ValidationError("circuit-schedule writes require an expected current program")
            if expected is not _UNSET:
                expected = normalize_schedule(name, expected, replacement=False)
        async with self._operation_lock:
            self._check_open()
            preview = await self._preview(name, value, expected)
            before, requested = preview.parameter, preview.requested
            if values_equal(name, stored_value(before), requested):
                return WriteResult(name, requested, before, before, None, "unchanged")

            returned = after = None
            status = None
            write_error = read_error = None
            try:
                response = await self._request("POST", "/parameters", {
                    "parameters": [{"name": name, "value": wire_value(name, requested)}]
                })
                status = response.status
                if status != 200:
                    raise HTTPError("POST", "/parameters", status, response.reason)
                returned = self._parameters(response, (name,))[name]
            except HargassnerError as exc:
                write_error = exc

            # A rejected/lost response does not prove the setter did not run.
            # Read recovery may re-authenticate with true, but never repeats POST.
            try:
                if name in SCHEDULE_PARAMETERS:
                    after, _ = await self._get_schedule_context(
                        name, expected_type=preview.circuit_type_id
                    )
                elif name in CIRCUIT_MODES:
                    after, _ = await self._get_mode_context(
                        name, expected_type=preview.circuit_type_id
                    )
                elif name in HEATING_CURVES:
                    after = await self._get_curve_context(name)
                else:
                    after = (await self._get_parameters((name,)))[name]
            except HargassnerError as exc:
                read_error = exc

            def matches(parameter: Parameter | None) -> bool:
                if (
                    parameter is None or parameter.type != before.type
                    or parameter.security_level != before.security_level
                    or parameter.unit != before.unit or parameter.active is not before.active
                ):
                    return False
                try:
                    if name in CIRCUIT_MODES:
                        validate_mode_parameter(parameter)
                    if name in SCHEDULE_PARAMETERS:
                        validate_schedule_parameter(parameter, active=True)
                    return values_equal(name, stored_value(parameter), requested)
                except ValidationError:
                    return False

            if write_error is not None or read_error is not None:
                result = WriteResult(name, requested, before, after, returned, "unknown", status)
                raise WriteOutcomeUnknownError(
                    result, write_error=write_error, read_error=read_error
                )
            if not matches(returned) or not matches(after):
                result = WriteResult(name, requested, before, after, returned, "mismatch", status)
                raise WriteMismatchError(result)
            return WriteResult(name, requested, before, after, returned, "verified", status)
