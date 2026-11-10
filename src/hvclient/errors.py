"""Home Assistant-independent errors, without response bodies or credentials."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import WriteResult


class HargassnerError(Exception):
    """Base error for the public library API."""


class ClientClosedError(HargassnerError):
    pass


class TransportError(HargassnerError):
    """Connection failure or timeout; a failed write may have been applied."""


class ProtocolError(HargassnerError):
    """Unexpected or malformed response. Raw response bodies are not retained."""


class HTTPError(HargassnerError):
    def __init__(self, method: str, path: str, status: int, reason: str) -> None:
        # Preserve firmware reason phrases, but never echo auth hex material if
        # a broken server includes it in an error. Do not retain response bodies.
        self.reason = re.sub(r"[0-9a-fA-F]{64,}", "<redacted>", reason)
        self.method = method
        self.path = path
        self.status = status
        super().__init__(f"{method} {path}: {status} {self.reason}")


class AuthenticationError(HargassnerError):
    pass


class CooldownError(AuthenticationError):
    pass


class ParameterAccessError(HargassnerError):
    """Requested parameter is not classified USER; no higher-level data exposed."""


class ParameterNotFoundError(HargassnerError):
    pass


class ValidationError(HargassnerError):
    pass


class WritePolicyError(ValidationError):
    """Writes are disabled or the name is outside the reviewed numeric policy."""


class WriteVerificationError(HargassnerError):
    """Contains only USER before/returned/read-back data; never replay a write."""

    def __init__(
        self,
        result: WriteResult,
        *,
        write_error: HargassnerError | None = None,
        read_error: HargassnerError | None = None,
    ) -> None:
        self.result = result
        self.write_error = write_error
        self.read_error = read_error
        super().__init__(
            f"parameter {result.name!r}: write {result.outcome}; "
            "inspect read-back before deciding whether to retry"
        )


class WriteMismatchError(WriteVerificationError):
    pass


class WriteOutcomeUnknownError(WriteVerificationError):
    pass
