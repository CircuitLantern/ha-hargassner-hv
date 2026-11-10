"""Parameters-only async Hargassner client, with no Home Assistant dependency."""

from .client import AsyncHargassnerClient
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
    WriteVerificationError,
)
from .models import (
    Choice,
    ControllerDateTime,
    Parameter,
    TimeRange,
    WeeklyTimeRanges,
    WritePreview,
    WriteResult,
)
from .policy import NUMERIC_USER_WRITES, WritePolicy

__all__ = [
    "NUMERIC_USER_WRITES",
    "AsyncHargassnerClient",
    "AuthenticationError",
    "Choice",
    "ClientClosedError",
    "ControllerDateTime",
    "CooldownError",
    "HTTPError",
    "HargassnerError",
    "Parameter",
    "ParameterAccessError",
    "ParameterNotFoundError",
    "ProtocolError",
    "TimeRange",
    "TransportError",
    "ValidationError",
    "WeeklyTimeRanges",
    "WriteMismatchError",
    "WriteOutcomeUnknownError",
    "WritePolicy",
    "WritePolicyError",
    "WritePreview",
    "WriteResult",
    "WriteVerificationError",
]
