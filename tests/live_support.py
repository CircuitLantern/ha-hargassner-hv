# SPDX-License-Identifier: MPL-2.0

"""Test-only opt-in and request guards; never installed as a console client."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from yarl import URL

from hvclient import PARAMETER_WRITES, AsyncHargassnerClient, WritePolicy

_READ_PATHS = frozenset({"/challenge", "/info", "/channels", "/pcb", "/parameters"})


@dataclass(frozen=True)
class LiveConfig:
    host: str = field(repr=False)
    port: int = 80


def live_config(environment: Mapping[str, str]) -> LiveConfig | None:
    """No opt-in means no host lookup, client construction, or network activity."""
    if environment.get("HVCLIENT_LIVE") != "1":
        return None
    if environment.get("CI") or environment.get("PYTEST_XDIST_WORKER"):
        raise ValueError("live tests must run manually, serially, outside CI/xdist")
    host = environment.get("HVCLIENT_LIVE_HOST", "").strip()
    if not host:
        raise ValueError("HVCLIENT_LIVE=1 requires an explicit HVCLIENT_LIVE_HOST")
    # Use the library's configuration validation; construction performs no I/O.
    AsyncHargassnerClient(host)
    return LiveConfig(host)


class LiveRequestBlocked(RuntimeError):
    """A fixed message only: never include bodies, cookies, or destinations."""


class LiveRequestGuard:
    def __init__(self, config: LiveConfig) -> None:
        self._base = URL.build(scheme="http", host=config.host, port=config.port)
        self.requests: Counter[tuple[str, str]] = Counter()
        self.authentication_checks = 0

    @staticmethod
    def _allowed(method: str, path: str) -> bool:
        return (method == "GET" and path in _READ_PATHS) or (
            method == "POST" and path == "/authenticate"
        )

    def check_request(self, method: str, path: str, body: Any) -> None:
        if not self._allowed(method, path):
            raise LiveRequestBlocked("live smoke tests prohibit this method/path")
        if method == "POST":
            if not (
                isinstance(body, dict) and set(body) == {"key", "daqOnly"}
                and body["daqOnly"] is True and isinstance(body["key"], str)
                and re.fullmatch(r"[0-9a-f]{128}", body["key"])
            ):
                raise LiveRequestBlocked("live authentication requires explicit daqOnly=true")
            self.authentication_checks += 1
        elif path == "/parameters" and body is not None:
            if not (
                isinstance(body, dict) and set(body) == {"parameters"}
                and isinstance(body["parameters"], list)
                and all(isinstance(name, str) for name in body["parameters"])
            ):
                raise LiveRequestBlocked("live parameter reads require a name selection")
        elif body is not None:
            raise LiveRequestBlocked("live snapshot reads must not have a body")

    async def on_request_start(self, session: Any, context: Any, params: Any) -> None:
        # A second guard checks the actual transport destination/method, even if
        # a call bypasses the library wrapper. No request bodies/headers retained.
        path = params.url.path
        if (
            not self._allowed(params.method, path)
            or params.url != self._base.with_path(path)
        ):
            raise LiveRequestBlocked("live transport rejected a method or destination")
        self.requests[params.method, path] += 1

    def trace_config(self) -> aiohttp.TraceConfig:
        trace = aiohttp.TraceConfig()
        trace.on_request_start.append(self.on_request_start)
        return trace


class PreviewOnlyClient(AsyncHargassnerClient):
    def __init__(
        self, config: LiveConfig, session: aiohttp.ClientSession, guard: LiveRequestGuard,
    ) -> None:
        super().__init__(
            config.host, config.port, session=session,
            write_policy=WritePolicy.only(*PARAMETER_WRITES),
            timeout=10, read_retries=0, authentication_attempts=2,
        )
        self._live_guard = guard

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        self._live_guard.check_request(method, path, body)
        return await super()._request(method, path, body)

    async def async_set_parameter(self, *args: Any, **kwargs: Any) -> Any:
        raise LiveRequestBlocked("live smoke tests prohibit applying parameter changes")
