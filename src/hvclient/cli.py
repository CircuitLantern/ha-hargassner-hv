"""Thin one-shot testing CLI: all protocol/validation work belongs to the library."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from .client import AsyncHargassnerClient
from .errors import HargassnerError, ValidationError, WriteVerificationError
from .models import WriteResult
from .policy import WritePolicy, decimal_number


def _number(text: str) -> int | Decimal:
    try:
        value = json.loads(text, parse_float=Decimal)
        decimal_number(value)
        return value
    except (ValueError, InvalidOperation, ValidationError) as exc:
        raise argparse.ArgumentTypeError("expected a finite JSON number") from exc


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))


def _write_report(result: WriteResult) -> dict[str, Any]:
    return {
        "name": result.name, "outcome": result.outcome, "requested": result.requested,
        "before": result.before.to_dict(), "write_status": result.write_status,
        "returned": result.returned.to_dict() if result.returned is not None else None,
        "read_back": result.after.to_dict() if result.after is not None else None,
        "read_back_matches": result.read_back_matches,
    }


async def _run(args: argparse.Namespace) -> int:
    policy = WritePolicy.only(args.name) if args.command == "set" else WritePolicy()
    async with AsyncHargassnerClient(
        args.host, args.port, timeout=args.timeout, write_policy=policy
    ) as client:
        if args.command == "list":
            parameters = await client.async_get_parameters()
            _print({"parameters": [parameter.to_dict() for parameter in parameters.values()]})
        elif args.command == "get":
            parameters = await client.async_get_parameters(args.names)
            _print({"parameters": [parameter.to_dict() for parameter in parameters.values()]})
        elif args.command == "channels":
            _print((await client.async_get_channels()).to_dict())
        elif args.command == "info":
            _print((await client.async_get_info()).to_dict())
        elif args.command == "pcb":
            _print((await client.async_get_pcb()).to_dict())
        else:
            expected = {} if args.expect is None else {"expected": args.expect}
            if not args.apply:
                preview = await client.async_preview_parameter_write(args.name, args.value, **expected)
                _print({
                    "outcome": "dry_run", "requested": preview.requested,
                    "parameter": preview.parameter.to_dict(),
                })
            else:
                result = await client.async_set_parameter(args.name, args.value, **expected)
                _print(_write_report(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test hvclient telemetry, information and USER parameters; always daqOnly=true.",
        allow_abbrev=False,
    )
    parser.add_argument("host", help="bare boiler IP or hostname")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=10.0)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("channels", help="read the numeric channel snapshot", allow_abbrev=False)
    commands.add_parser(
        "info", help="read device/firmware info (includes private identifiers)", allow_abbrev=False
    )
    commands.add_parser("pcb", help="read board temperature/voltage", allow_abbrev=False)
    commands.add_parser(
        "list", help="read the USER catalog, including inactive settings", allow_abbrev=False
    )
    get = commands.add_parser("get", help="read selected USER API names", allow_abbrev=False)
    get.add_argument("names", nargs="+")
    set_command = commands.add_parser(
        "set", help="preview a reviewed numeric write, or apply it", allow_abbrev=False
    )
    set_command.add_argument("name")
    set_command.add_argument("value", type=_number)
    set_command.add_argument("--apply", action="store_true", help="send one update and verify")
    set_command.add_argument("--expect", type=_number, help="expected current value; required to apply")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "set" and args.apply and args.expect is None:
        parser.error("--apply requires --expect CURRENT_VALUE")
    try:
        return asyncio.run(_run(args))
    except WriteVerificationError as exc:
        _print(_write_report(exc.result))
        print(str(exc), file=sys.stderr)
        return 1
    except (ValidationError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except HargassnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted; if applying, inspect the current value before retrying", file=sys.stderr)
        return 130
