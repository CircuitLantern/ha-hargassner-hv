# Independent Hargassner client library

`hargassner-hvclient` provides the `hvclient` Python package. It is an async,
client for the local Hargassner HTTP API, with no Home Assistant dependency.
It supports USER parameters plus **read-only `/channels`, `/info`, and `/pcb`**.
Python 3.11+ and aiohttp 3.12+ are required.

It **always authenticates with `daqOnly=true`**, including recovery. There is
no SERVICE-mode flag or fallback, higher-level catalog option, raw endpoint
method, or mode-restoration request. It cannot detect or undo SERVICE entered
by another client, nor guarantee undocumented behavior on arbitrary firmware.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

The package is independently installable. It imports neither the research tools
nor Home Assistant, and it has no storage, polling daemon, or background tasks.

## Read channels, device info, and board diagnostics

```python
from hvclient import AsyncHargassnerClient

async def read_snapshots(host):
    async with AsyncHargassnerClient(host) as client:
        channels = await client.async_get_channels()
        info = await client.async_get_info()
        pcb = await client.async_get_pcb()
        return channels, info.software_version, pcb.internal_temperature
```

All three methods use GET with no body and share the same authentication,
cookie refresh, operation lock, timeouts, response-size bound, and bounded read
recovery as parameter reads. They work with the default read-only write policy.
They do not load the parameter catalog or enable any writes.

| Method | Return type | Data |
|---|---|---|
| `async_get_channels()` | `ChannelSnapshot`, an immutable numeric `Mapping` | Dynamic channel names and finite numbers, including zero and integer-valued states/counters |
| `async_get_info()` | `SystemInfo` | `software_version`, `mac`, `system_id`, `commission_number`, `article_serial_number` |
| `async_get_pcb()` | `PcbInfo` | `internal_temperature` and `internal_voltage`, both finite numbers |

Use `channels[name]`, `.get()`, `.items()`, or `.to_dict()`. Channel names are
discovered from each snapshot, not hard-coded. An empty object is a valid empty
snapshot; booleans, strings, nulls, and non-finite readings are rejected rather
than coerced to numbers. Units, enum meanings, counter behavior, and measured
versus target semantics are **not inferred** from names or values. Channels do
not have the parameter catalog's USER/security-level classification and are not
filtered by it; **parameter** reads remain USER-only.

`SystemInfo` known fields accept strings, missing fields, or null. Missing/null
fields remain `None`; no replacement identity is generated. `PcbInfo` requires
both numeric readings and never substitutes zero for a missing sensor. Both
models preserve unknown fields as immutable `.raw` metadata. Every `.to_dict()`
returns a detached wire-shaped copy; no snapshot mutates later as polling occurs.

**Privacy:** `/info` contains private device identifiers. Known identity fields
and raw extensions are hidden from `SystemInfo`'s representation, but explicit
`.to_dict()` and CLI `info` output contain the original identifiers. Sanitize
them before publishing logs or Home Assistant diagnostics.

There are no channel/identity/PCB setters. The request boundary allows only the
exact GET roots for these additions: no body, other HTTP method, query string,
or subpath. In particular `/info/systemid/generate` remains forbidden even with
GET, because its firmware handler can mutate state regardless of method.

A `/channels` 404 raises `HTTPError`, without switching authentication modes or
trying another endpoint. It may indicate unsupported firmware or the firmware's
global `daqOnly` flag having been changed by another client. No automatic
stronger login is attempted. A 401 can trigger one fresh `daqOnly=true` login,
just like a parameter read.

## Read parameters

```python
import asyncio
from hvclient import AsyncHargassnerClient

async def main():
    async with AsyncHargassnerClient("<boiler-ip>") as client:
        parameters = await client.async_get_parameters()  # USER only, including inactive
        daytime = await client.async_get_parameter("4")
        selected = await client.async_get_parameters(["4", "5"])
        print(daytime.value, daytime.unit)
        print(daytime.to_dict())  # detached wire-shaped metadata/value copy

asyncio.run(main())
```

Names are API names, not menu labels, channel names, or translated descriptions.
The server has no level-filtered catalog endpoint: a full GET receives the
catalog, then discards every non-USER row locally. Selecting a non-USER name
raises `ParameterAccessError`; missing selected names raise
`ParameterNotFoundError`. There is no INSTALLER/PLUMBER/SERVICE/DEVELOPER option.
Nested catalog `children` are not exposed through USER metadata.

`Parameter` and its values/metadata are immutable. Scalar types are ordinary
Python int/float/bool/str. Other USER values are:

- `Choice`, or tuples of `Choice` for multiple-choice values; IDs and labels
  are separate, and the available choices are in `Parameter.choices`.
- Tuples of `TimeRange` for daily schedules. Strings such as `24:00:00` and
  zero-length slots are preserved, not converted to `datetime.time`.
- Tuples of `WeeklyTimeRanges`, preserving numeric weekday groups without
  assuming an unverified weekday mapping or overnight interpretation.
- `ControllerDateTime`, preserving raw integer components, including invalid/
  unset zero and −1 components. No timezone or replacement date is assumed.
- Unknown future USER types remain read-only immutable JSON containers.

Live numeric limits, increments, active flags, boolean labels, help text, and
unknown metadata are preserved. `active` is applicability, not current heating
state; USER is not a network permission or safety guarantee.

## Explicitly permitted writes

Writes are **denied by default**. This initial release supports only the same
11 reviewed numeric temperature settings as the investigation setter: `4`, `5`,
`7`, `8`, `HP4`, `HP5`, `2`, `HP2`, `11`, `12`, and `13`. The caller must choose
a subset with `WritePolicy.only(...)`; it cannot opt into arbitrary names.

```python
from hvclient import AsyncHargassnerClient, WritePolicy

async def intentional_change(host, requested, expected):
    async with AsyncHargassnerClient(host, write_policy=WritePolicy.only("4")) as client:
        preview = await client.async_preview_parameter_write("4", requested, expected=expected)
        # A preview never sends a parameter POST. Confirmation belongs to the caller.
        result = await client.async_set_parameter("4", requested, expected=expected)
        return result.after.value
```

Every proposed write re-reads current metadata, requires USER, active=true,
the reviewed type and Celsius unit, and validates finite values, live bounds,
and the minimum-anchored increment grid without rounding/clamping. INTEGER
settings require int, not bool or float. False and zero are not treated as
missing values; booleans are deliberately not numeric inputs.

`expected` is an optional numeric stale-value check, not atomic compare-and-set.
Unchanged values return `outcome="unchanged"` without a POST. A change sends
only one sparse `name`/`value` entry, checks the POST-returned object, and then
performs a separate GET. Success returns `WriteResult` with before/returned/after
parameters, outcome, and status. Persistence and physical effects are not proven.

Schedules, dates, choices, text, ordinary booleans, ignition, mode controls,
and installer/service/developer tuning are **read-only or excluded**, not
implicitly writable because their catalog level says USER. No batch setter,
automatic rollback, or stored-request replay is provided.

### Unverified outcomes and errors

`HargassnerError` is the common base. Specific errors distinguish transport,
HTTP status/reason, authentication/cooldown, malformed JSON/protocol, missing or
non-USER parameters, validation, and write policy refusals.

- `WriteMismatchError`: POST/GET values do not match the request.
- `WriteOutcomeUnknownError`: a write response or read-back could not be verified.

Both derive from `WriteVerificationError` and carry `.result` with observed
USER data and optional `.write_error`/`.read_error`. Even a matching read-back
after a lost/rejected POST response remains unknown, not a successful write.
The parameter POST is **never retried**, including after 401. Inspect the result
and controller before deciding whether another intentional write is appropriate.

Cancellation propagates and releases the operation lock; a cancelled write may
already have applied. Inspect the current value explicitly after cancellation.
Exceptions/logs omit response bodies and authentication material.

## Sessions and lifecycle

Pass `session=your_aiohttp_session` to borrow a session. The client never closes
a borrowed session; it closes its own session on `async_close()` or context exit.
No I/O or session creation occurs in the constructor. Close is idempotent and a
closed client cannot be reused. An empty selection makes no network requests.

One lock serializes authentication, reads, and each complete write/read-back
operation. It coordinates only this instance, not other clients or the local
controller. Authentication uses SHA-512, validates challenges and freshly issued
cookies, respects the five-second challenge cooldown, and has bounded attempts.
Reads recover once from 401 using true authentication. Read transport retries
are bounded/configurable; HTTP errors are not blindly retried.

Requests use explicit per-client cookies (overriding stale shared-jar tokens),
fixed method/path pairs, explicit JSON serialization, disabled redirects and
per-request middleware, finite request timeouts, and a response size limit.
Owned sessions have a dummy cookie jar and do not trust proxy environment variables.
Borrowed sessions must be configured by their owner for a trusted direct LAN
connection; their connector, tracing, and proxy policies remain caller-owned.
Never expose the boiler's plain-HTTP endpoint to an untrusted network.

Constructor controls: `port=80`, `timeout=10`, `read_retries=1` (0–3),
`authentication_attempts=3` (1–3), and `max_response_bytes=2097152` (up to 8 MiB).
There is deliberately no security-level or authentication-mode option.

## Thin testing CLI

```bash
hvctl <boiler-ip> channels
hvctl <boiler-ip> info                                  # includes private device IDs
hvctl <boiler-ip> pcb
hvctl <boiler-ip> list
hvctl <boiler-ip> get 4 5
hvctl <boiler-ip> set 4 21                        # preview only
hvctl <boiler-ip> set 4 21 --apply --expect 20.5   # intentional write
```

These are syntax examples, not recommended temperatures. `--port`/`--timeout`
precede the command. `python -m hvclient` invokes the same `hvctl` client.
All `channels`, `info`, and `pcb` commands
are read-only and print their wire-shaped JSON objects. The CLI only parses
arguments, calls the library, confirms writes via flags, and prints JSON/errors;
it implements no authentication, transport, metadata validation, or write policy
of its own. CLI application requires `--expect` in addition to `--apply`.
Exit codes: 0 success/preview/unchanged, 1 operational/unverified failure,
2 argument/policy/validation refusal, 130 interrupted.

The existing probe/setter remain separate historical investigation tools. They
are not imported by the library or the intended Home Assistant integration.

## Verification and scope

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

Tests use synthetic data and loopback mock HTTP servers, not a boiler. The
[earlier live round trip](parameter-setter.md#live-validation--2026-11-09)
validated the research setter, **not this new async implementation**. No live
library requests or writes are implied by installation or tests. The added
telemetry/info/PCB decoders are also checked against local captured JSON, not
new requests. A Home Assistant integration, other device routes, non-GET
telemetry/identity operations, higher parameter levels, and structured writes
are outside this implementation.
