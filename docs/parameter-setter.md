# Guarded parameter setter

[`tools/set_parameter.py`](../tools/set_parameter.py) is a one-shot investigation
tool, similar to the read-oriented probe. It uses Python's standard library and
reuses the probe's SHA-512 authentication and HTTP transport. It is **not** the
independent async library or its thin CLI.
For the implemented independent library and its thin `hvctl` testing
CLI, see [the library reference](library.md). This older tool remains separate
investigation tooling and is not imported by the library.

## Usage

Use the **API `name`**, not a manual menu label or channel name. For example,
`4` is circuit 1's daytime room target (menu Hk1-2).

```bash
# Preview only: authenticate with daqOnly=true, fetch live metadata, validate.
python3 tools/set_parameter.py <boiler-ip> 4 21

# Only if the preview reported 20.5 as the current value and the change is intended:
python3 tools/set_parameter.py <boiler-ip> 4 21 --apply --expect 20.5

# Show supported names and connection options; no network access.
python3 tools/set_parameter.py --help
```

The values above illustrate syntax, **not recommended settings**. Preview does
not send a parameter update. `--apply` is the explicit confirmation; there is no
interactive prompt. It also requires `--expect` so the value just read from the
controller must match the value the operator reviewed. This is a client-side
stale-value check, **not an atomic compare-and-set**: another client or the local
controller can still change a setting between the GET and POST.

`--port` defaults to 80 and `--timeout` to 10 seconds. Pass a bare IP/hostname,
not an HTTP URL. Values are finite JSON numbers: use an integer such as `60`,
not `60.0`, for an INTEGER parameter. False, strings, arrays, and objects are
not accepted as numbers. There are no configuration or cookie files.

## Explicit parameter policy

The initial allowlist contains **11 numeric settings**, selected by their known
meaning rather than by accepting everything marked USER:

| API name | Meaning | Required type |
|---|---|---|
| `4` / `5` | Circuit 1 comfort / setback room target | FLOAT |
| `7` / `8` | Circuit 2 comfort / setback room target | FLOAT |
| `HP4` / `HP5` | Circuit A comfort / setback room target | FLOAT |
| `2` / `HP2` | DHW cylinder 1 / A target | INTEGER |
| `11` | All-circuit outdoor shutdown threshold | INTEGER |
| `12` / `13` | Day- / night-setback outdoor shutdown threshold | INTEGER |

Before writing, the tool requires a single matching live parameter object with:

- The expected type and USER classification, and `active` exactly true.
- Unit `°C` and finite numeric current value, minimum, maximum, and increment.
- Valid ordered bounds and a positive increment.
- A correctly typed requested value within the **live** bounds and aligned to
  the increment, anchored at the minimum. Decimal arithmetic avoids binary
  floating-point modulo errors. The value is not rounded or clamped.

Inactive parameters are refused even if the corresponding optional circuit or
cylinder appears in the catalog. Unknown names, type/classification changes,
and missing metadata fail closed. There is no `--force`, arbitrary-name
override, or inactive-parameter bypass.

Schedules, dates, choices, display configuration, room-unit offsets,
installer/service/developer tuning, mode controls, ignition, resets, and raw
actuators are outside this initial tool's policy. Extending the allowlist
requires a code review of the setting's semantics and serializer, not merely
finding another USER entry.

An allowlist entry is **not** proof of a safe temperature or successful live
write. Targets and shutdown thresholds can affect heating immediately. DHW
hygiene/scalding constraints are not defined by the controller's numeric range.
See the [English parameter guide](parameter-guide.md) for dependencies and
manual-backed meanings.

## SERVICE-mode exclusion

- Every authentication explicitly sends **`"daqOnly": true`**. There is no
  `--full` or `--allow-service-mode` option and no fallback to false/omission.
- A request-boundary guard also rejects false, missing, or incorrectly typed
  `daqOnly` values, including if inherited authentication were called wrongly.
- The only permitted method/path pairs are GET `/challenge`, POST
  `/authenticate`, and GET/POST `/parameters`. Parameter requests contain only
  one allowlisted name; updates contain only its `name` and `value`.
- The transport does not follow redirects, so authentication or a parameter
  body is not forwarded to a different endpoint/host.
- Read-session recovery authenticates with true again. Write failures never
  switch modes or retry with stronger access.

Thus this tool **never requests SERVICE mode**. This does not detect or undo
SERVICE already entered by another client, nor prevent another client from
changing the boiler's global state. It cannot guarantee undocumented behavior
on arbitrary firmware. The implementation evidence is from 2.7.4 and the live
read baseline is 3.4.0; physical effects and persistence still need controlled
validation. No HTTP mode-restoration request is attempted on exit.

## Write and verification behavior

1. Authenticate with true and fetch the selected parameter using a GET JSON body.
2. Validate policy, metadata, value, and `--expect` if supplied.
3. If not applying, return a `dry_run` report. If the requested value already
   equals the current value when applying, return `unchanged` without a POST.
4. Otherwise send exactly one sparse update:

   ```json
   {"parameters":[{"name":"4","value":21.0}]}
   ```

5. Inspect the returned parameter, then perform a separate GET and compare its
   numeric value. HTTP 200 alone does not establish that the setting changed.

The tool sends **at most one parameter POST per invocation**. Read connection
retries and the probe's bounded authentication/cooldown handling remain enabled.
A read that receives 401 can authenticate with true once and retry. A parameter
POST is never replayed, even after 401, a timeout, or disconnect.

After a failed/lost POST response, the tool still attempts read-back. Even if
the observed value matches, a failed POST or invalid response remains
`unverified` and exits nonzero. Inspect the report and local controller before
deciding what to do next; do not blindly repeat the command. No automatic
rollback, persistence test/reboot, or physical-effect assertion is performed.

stdout contains one JSON report with the before/requested values, live limits,
outcome, and any returned/read-back values. Errors/warnings go to stderr. Cookies,
authentication keys, and challenges are not included in reports.

| Exit code | Meaning |
|---|---|
| `0` | Valid preview, unchanged/no POST, or both POST response and fresh GET match |
| `1` | Authentication/transport/protocol failure or an unverified write |
| `2` | Invalid arguments or local policy/validation refusal |
| `130` | Interrupted; if a write was attempted its outcome may be unknown |

Read-back establishes only the observed stored value at that moment, not a
physical effect, ownership of a concurrent change, or persistence after reboot.
Run only on a trusted LAN; this API uses plain HTTP and a firmware-embedded salt.

## Offline validation

```bash
python3 -B -m unittest discover -s tests -p test_set_parameter.py -v
```

The tests replace HTTP connections with a stateful in-memory device and inspect
the serialized requests. They cover authentication safety and recovery, request
restrictions, live-metadata validation, sparse writes, silent rejection, mismatched and
missing read-back, uncertain write outcomes, no replay, and secret-free output.
They do not contact a boiler and do not establish live write compatibility.

## Live validation — 2026-11-09

At the operator's explicit request, the tool completed one limited round-trip
test on the LAN boiler previously identified as `smart_hv_3.4.0`. Identity/version
was not fetched again during this test. Only the zone-1 daytime room target,
API `name="4"` (menu Hk1-2), was changed.

| Step | Before | Requested | POST-returned value | Separate GET value | Outcome |
|---|---|---|---|---|---|
| Preview | 20.5 °C | 21.0 °C | No parameter POST | Initial GET: 20.5 °C | `dry_run` |
| Raise by 0.5 °C | 20.5 °C | 21.0 °C | 21.0 °C; HTTP 200 | 21.0 °C | `verified` |
| Restore by lowering 0.5 °C | 21.0 °C | 20.5 °C | 20.5 °C; HTTP 200 | 20.5 °C | `verified` |

The commands were, with the local address replaced here by a placeholder:

```bash
python3 -B tools/set_parameter.py <boiler-ip> 4 21
python3 -B tools/set_parameter.py <boiler-ip> 4 21 --apply --expect 20.5
python3 -B tools/set_parameter.py <boiler-ip> 4 20.5 --apply --expect 21
```

All three commands exited successfully. The live metadata reported FLOAT,
USER, active, °C, range 0–30, and increment 0.5; the tool validated these before
each update. Both writes returned the requested value and a subsequent GET
matched it. **The final observed setting was restored to 20.5 °C.**

Every login explicitly used `daqOnly=true`; no SERVICE-mode request, write
retry, reboot, or additional parameter change was performed. There was no
independent mode measurement or physical heating/room-temperature observation.
This establishes immediate write/read-back for one setting on one unit, **not**
reboot persistence, other allowlisted settings, arbitrary firmware compatibility,
or the physical effect of the change. Authentication material and device
identifiers are intentionally omitted from this record.
