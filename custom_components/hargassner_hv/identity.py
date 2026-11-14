# SPDX-License-Identifier: Apache-2.0
"""Stable, hashed device identity obtained from the API, never from a host."""

import hashlib
import re

from .hvclient import SystemInfo


class IdentityError(ValueError):
    """The device cannot be identified or does not match the configured device."""


def identities(info: SystemInfo) -> dict[str, str]:
    """Prefer the hardware MAC; retain opaque API/serial fallbacks when valid."""
    result = {}
    mac = re.sub(r"[:-]", "", info.mac or "").lower()
    if (re.fullmatch(r"[0-9a-f]{12}", mac) and mac not in {"0" * 12, "f" * 12}
            and not int(mac[:2], 16) & 1):
        result["mac"] = mac
    for source, value in (("system_id", info.system_id), ("serial", info.article_serial_number)):
        if (isinstance(value, str) and 3 <= len(value.strip()) <= 255
                and value.strip().casefold() not in {"unknown", "none", "null", "unset", "000"}
                and set(value.strip()) != {"0"}
                and all(character.isprintable() for character in value)):
            result[source] = value.strip()
    return {source: source + ":" + hashlib.sha256(value.encode()).hexdigest()
            for source, value in result.items()}


def identity(info: SystemInfo, source: str | None = None) -> tuple[str, str]:
    candidates = identities(info)
    if source is not None:
        if source not in candidates:
            raise IdentityError("Configured identity is unavailable")
        return source, candidates[source]
    if not candidates:
        raise IdentityError("No stable device identity is available")
    return next(iter(candidates.items()))
