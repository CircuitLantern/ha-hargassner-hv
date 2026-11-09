#!/usr/bin/env python3
"""One-shot, guarded numeric parameter setter; always authenticates daqOnly=true.

This is investigation tooling, not the planned reusable client library. No
SERVICE login, raw endpoint, arbitrary parameter, or write-retry option exists.
Run without --apply first; applying also requires --expect CURRENT_VALUE.
See docs/parameter-setter.md for the policy and physical-operation limitations.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

if __package__:
    from .probe import Boiler, ProbeError, Result
else:
    from probe import Boiler, ProbeError, Result


@dataclass(frozen=True)
class AllowedParameter:
    type: str
    description: str


# Deliberately not derived from every USER entry. In particular, USER includes
# ignition and other command-like settings. Extend only after semantic review.
ALLOWED_PARAMETERS = {
    "4": AllowedParameter("FLOAT", "Circuit 1 comfort room target"),
    "5": AllowedParameter("FLOAT", "Circuit 1 setback room target"),
    "7": AllowedParameter("FLOAT", "Circuit 2 comfort room target"),
    "8": AllowedParameter("FLOAT", "Circuit 2 setback room target"),
    "HP4": AllowedParameter("FLOAT", "Circuit A comfort room target"),
    "HP5": AllowedParameter("FLOAT", "Circuit A setback room target"),
    "2": AllowedParameter("INTEGER", "DHW cylinder 1 target"),
    "HP2": AllowedParameter("INTEGER", "DHW cylinder A target"),
    "11": AllowedParameter("INTEGER", "All circuits outdoor shutdown threshold"),
    "12": AllowedParameter("INTEGER", "Day-setback outdoor shutdown threshold"),
    "13": AllowedParameter("INTEGER", "Night-setback outdoor shutdown threshold"),
}


class PolicyError(ProbeError):
    """Local policy or typed-value validation failure."""


def decimal_number(value: Any) -> Decimal:
    if type(value) not in (int, float, Decimal):
        raise PolicyError("expected a JSON number (not a boolean or string)")
    number = Decimal(str(value))
    if not number.is_finite():
        raise PolicyError("numbers must be finite")
    return number


def parse_number(text: str) -> int | Decimal:
    try:
        value = json.loads(text, parse_float=Decimal)
        decimal_number(value)
        return value
    except (ValueError, InvalidOperation, PolicyError) as exc:
        raise argparse.ArgumentTypeError("expected a finite JSON number") from exc


def allowed_parameter(name: str) -> AllowedParameter:
    try:
        return ALLOWED_PARAMETERS[name]
    except KeyError as exc:
        raise PolicyError(
            f"parameter {name!r} is not allowlisted; use an API name from --help. "
            "There is no arbitrary-name override."
        ) from exc


def typed_number(value: Any, type_name: str) -> Decimal:
    number = decimal_number(value)
    if type_name == "INTEGER" and type(value) is not int:
        raise PolicyError("INTEGER parameters require a JSON integer")
    return number


def json_document(result: Result) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON constant")

    try:
        return json.loads(result.raw, parse_float=Decimal, parse_constant=reject_constant)
    except (ValueError, InvalidOperation) as exc:
        # Do not include an unexpected authentication body or full catalog.
        raise ProbeError(f"{result.method} {result.path}: invalid JSON response") from exc


def parameter_object(result: Result, name: str) -> dict[str, Any]:
    if result.status != 200:
        raise ProbeError(f"{result.method} {result.path}: {result.status} {result.reason!r}")
    document = json_document(result)
    entries = document.get("parameters") if isinstance(document, dict) else None
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(entries[0], dict)
        or entries[0].get("name") != name
        or "value" not in entries[0]
    ):
        raise ProbeError(f"{result.method} {result.path}: expected exactly parameter {name!r}")
    return entries[0]


def validate_change(name: str, parameter: dict[str, Any], value: Any) -> int | float:
    policy = allowed_parameter(name)
    if parameter.get("name") != name or parameter.get("type") != policy.type:
        raise PolicyError("parameter name/type does not match the reviewed policy")
    if parameter.get("securityLevel") != "USER":
        raise PolicyError("parameter classification has changed; expected USER")
    if parameter.get("active") is not True:
        raise PolicyError("parameter is inactive or its active state is unknown")
    if parameter.get("unit") != "°C":
        raise PolicyError("parameter unit has changed; expected °C")
    typed_number(parameter.get("value"), policy.type)
    requested = typed_number(value, policy.type)
    try:
        minimum, maximum, increment = (
            decimal_number(parameter[field]) for field in ("minimum", "maximum", "increment")
        )
    except KeyError as exc:
        raise PolicyError("live minimum, maximum, and increment are required") from exc
    if minimum > maximum or increment <= 0:
        raise PolicyError("invalid live limits/increment")
    if not minimum <= requested <= maximum:
        raise PolicyError(f"value is outside the live range {minimum} to {maximum}")
    try:
        # Anchor the grid at the advertised minimum, without binary float modulo.
        if (requested - minimum) % increment != 0:
            raise PolicyError(f"value is not aligned to the live increment {increment}")
    except InvalidOperation as exc:
        raise PolicyError("numeric precision exceeds the supported validation range") from exc
    try:
        wire_value = int(requested) if policy.type == "INTEGER" else float(requested)
        representable = math.isfinite(wire_value) and Decimal(str(wire_value)) == requested
    except (OverflowError, ValueError):
        representable = False
    if not representable:
        raise PolicyError("value cannot be represented faithfully as a JSON number")
    return wire_value


class ParameterSetter(Boiler):
    """Reuse probe transport/auth, with a second guard at the request boundary.

    http.client does not follow redirects. Even inherited authentication can
    send only a complete daqOnly=true body; false/missing flags fail locally.
    """

    def __init__(self, host: str, port: int, timeout: float, *, apply: bool = False) -> None:
        super().__init__(host, port, timeout)
        self.apply = apply
        self.write_attempted = False

    def authenticate(self, daq_only: bool = True) -> None:
        if daq_only is not True:
            raise PolicyError("SERVICE authentication is forbidden; daqOnly must be true")
        super().authenticate(daq_only=True)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float | None = None,
        retry: bool = False,
    ) -> Result:
        if (method, path) == ("GET", "/challenge") and body is None:
            pass
        elif (method, path) == ("POST", "/authenticate"):
            if (
                not isinstance(body, dict)
                or set(body) != {"key", "daqOnly"}
                or body["daqOnly"] is not True
                or not isinstance(body["key"], str)
                or re.fullmatch(r"[0-9a-f]{128}", body["key"]) is None
                or retry
            ):
                raise PolicyError("only explicit daqOnly=true authentication is permitted")
        elif path == "/parameters" and method in {"GET", "POST"}:
            entries = body.get("parameters") if isinstance(body, dict) else None
            if (
                not isinstance(body, dict)
                or set(body) != {"parameters"}
                or not isinstance(entries, list)
                or len(entries) != 1
            ):
                raise PolicyError("only single-parameter requests are permitted")
            if method == "GET":
                if not isinstance(entries[0], str):
                    raise PolicyError("GET requires one API parameter name")
                allowed_parameter(entries[0])
            else:
                entry = entries[0]
                if (
                    not isinstance(entry, dict)
                    or set(entry) != {"name", "value"}
                    or not isinstance(entry["name"], str)
                ):
                    raise PolicyError("writes require exactly one sparse name/value entry")
                policy = allowed_parameter(entry["name"])
                typed_number(entry["value"], policy.type)
                if not self.apply or self.daq_only is not True or not self.cookie:
                    raise PolicyError("write requires --apply and a daqOnly=true session")
                if retry or self.write_attempted:
                    raise PolicyError("parameter writes must never be retried/replayed")
                self.write_attempted = True
        else:
            raise PolicyError(f"request forbidden: {method} {path}")

        result = super().request(method, path, body, timeout=timeout, retry=retry)
        if (method, path, result.status) == ("GET", "/challenge", 200):
            document = json_document(result)
            challenge = document.get("challenge") if isinstance(document, dict) else None
            if not isinstance(challenge, str) or re.fullmatch(r"[0-9a-f]{64}", challenge) is None:
                raise ProbeError("GET /challenge: expected a 64-character lowercase hex challenge")
        return result

    def read_parameter(self, name: str) -> dict[str, Any]:
        body = {"parameters": [name]}
        result = self.request("GET", "/parameters", body, retry=True)
        if result.status == 401:
            # One read recovery, never a full/SERVICE login or a write replay.
            self.authenticate()
            result = self.request("GET", "/parameters", body, retry=True)
        return parameter_object(result, name)


def set_parameter(
    boiler: ParameterSetter, name: str, value: Any, *, expected: Any = None
) -> tuple[dict[str, Any], int]:
    policy = allowed_parameter(name)
    if boiler.apply and expected is None:
        raise PolicyError("--apply also requires --expect CURRENT_VALUE")
    boiler.authenticate()
    before = boiler.read_parameter(name)
    requested = validate_change(name, before, value)
    current = typed_number(before["value"], policy.type)
    if expected is not None and decimal_number(expected) != current:
        raise PolicyError(
            f"current value {current} does not match --expect {expected}; no write sent"
        )
    report = {
        "outcome": "dry_run",
        "authentication": "daqOnly=true",
        "name": name,
        "description": policy.description,
        "type": policy.type,
        "unit": before["unit"],
        "minimum": before["minimum"],
        "maximum": before["maximum"],
        "increment": before["increment"],
        "before": before["value"],
        "requested": requested,
    }
    if not boiler.apply:
        return report, 0
    if current == decimal_number(requested):
        report["outcome"] = "unchanged"
        return report, 0

    write_error = None
    try:
        response = boiler.request(
            "POST", "/parameters", {"parameters": [{"name": name, "value": requested}]}
        )
        report["write_status"] = response.status
        returned = parameter_object(response, name)
        returned_number = typed_number(returned["value"], policy.type)
        report["returned"] = returned["value"]
        if returned_number != decimal_number(requested):
            write_error = "POST returned a different value (HTTP success is not verification)"
    except (ProbeError, http.client.HTTPException) as exc:
        write_error = str(exc)

    # Read back even after a timeout/rejection: the write may already have run.
    # Never re-submit it, roll it back, or claim a failed response proved no change.
    try:
        read_back = boiler.read_parameter(name)
        observed = typed_number(read_back["value"], policy.type)
        report["read_back"] = read_back["value"]
        report["read_back_matches"] = observed == decimal_number(requested)
        report["outcome"] = (
            "verified" if not write_error and report["read_back_matches"] else "unverified"
        )
    except (ProbeError, http.client.HTTPException) as exc:
        report["outcome"] = "unverified"
        report["read_back_error"] = str(exc)
    if write_error:
        report["write_error"] = write_error
    return report, 0 if report["outcome"] == "verified" else 1


def build_parser() -> argparse.ArgumentParser:
    supported = "\n".join(
        f"  {name:>4}  {p.description}" for name, p in ALLOWED_PARAMETERS.items()
    )
    parser = argparse.ArgumentParser(
        description="Preview or set one reviewed numeric Smart HV parameter; always daqOnly=true.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        epilog=(
            "Supported API names (not menu labels):\n" + supported + "\n\n"
            "Without --apply, only authenticate and read. Applying requires --expect.\n"
            "No SERVICE login, arbitrary-name override, redirect, or write retry.\n"
            "Writes can affect heating immediately. See docs/parameter-setter.md."
        ),
    )
    parser.add_argument("host", help="boiler IP or hostname (no URL)")
    parser.add_argument("name", help="allowlisted API parameter name")
    parser.add_argument("value", type=parse_number, help="requested finite JSON number")
    parser.add_argument("--port", type=int, default=80, help="HTTP port (default 80)")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    parser.add_argument("--apply", action="store_true", help="send one update after validation")
    parser.add_argument("--expect", type=parse_number, help="required current value for --apply")
    return parser


def print_report(report: dict[str, Any]) -> None:
    def encode(value: Any) -> int | float:
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        raise TypeError(f"cannot encode {type(value).__name__}")

    print(json.dumps(report, indent=2, sort_keys=True, default=encode, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply and args.expect is None:
        parser.error("--apply requires --expect CURRENT_VALUE")
    if not 1 <= args.port <= 65535 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("port must be 1..65535 and timeout must be finite and positive")
    if not args.host or any(char in args.host for char in "/?#@\r\n\t "):
        parser.error("host must be an IP or hostname, not a URL")
    boiler = ParameterSetter(args.host, args.port, args.timeout, apply=args.apply)
    try:
        report, code = set_parameter(boiler, args.name, args.value, expected=args.expect)
        print_report(report)
        if code:
            print(
                "write unverified: inspect read-back and the controller; do not blindly repeat",
                file=sys.stderr,
            )
        return code
    except PolicyError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (ProbeError, http.client.HTTPException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        message = (
            "interrupted; write outcome may be unknown, inspect before retrying"
            if boiler.write_attempted else "interrupted"
        )
        print(message, file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
