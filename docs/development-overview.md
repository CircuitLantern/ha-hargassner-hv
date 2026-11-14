<!-- SPDX-License-Identifier: Apache-2.0 -->

# Hargassner Smart HV — HTTP API analysis and async client

This development/research overview preserves the project's protocol knowledge
and tooling context. See the [public overview](../README.md) and
[installation guide](home-assistant.md) for the current bundled-SDK installation.

Reverse-engineering notes and tooling for the HTTP service exposed on TCP/80 by
the Hargassner Smart HV 2.7.4 wood-log boiler firmware.
The independent async Python library supports **USER parameters**, six reviewed
**heating-curve slope** and three **circuit-mode exceptions**, and read-only
**channels, device info, and PCB diagnostics**.
The separate **Home Assistant custom integration** provides operational
telemetry sensors, diagnostic setting readbacks, opt-in climate/number/select controls,
and a dedicated circuit-program action through
the same library. Its Marketplace-compatible layout targets HA 2026.11.1;
the SDK sources now ship inside the component, without a PyPI release.
The protocol notes also distinguish
observations from a 3.4.0 unit.

## Contents

| Path | Purpose |
|---|---|
| `custom_components/hargassner_hv/hvclient/` | Single-source, independently installable async SDK and CLI; no Home Assistant dependency. |
| `docs/library.md` | Library installation, Python API, write policy, lifecycle, errors, and CLI usage. |
| `custom_components/hargassner_hv/` | Separate Home Assistant adapter: config flow, polling, sensors, opt-in controls, and program action. |
| `docs/home-assistant.md` | Integration capabilities, development installation, entities/actions, testing, and release limitations. |
| `docs/channel-guide.md` | Curated operational channel meanings, native units, validity limits, and firmware evidence. |
| `tools/prepare_ha_dist.py` | Build a private source-only manual-install HA bundle, instructions, and checksums under `dist/`. |
| `docs/boiler-http-api.md` | Reverse-engineered API, auth, routing, and endpoint reference. |
| `docs/parameter-guide.md` | English parameter meanings, plant terminology, and a crosswalk to the German electrical/service manuals. |
| `docs/client-development-plan.md` | Independent async library, optional thin CLI, and Home Assistant integration plan; no custom GUI or data store. |
| `docs/climate-development-plan.md` | Per-circuit climate feature: approved HVAC/preset mapping, persistent targets, state safety, implementation, and validation gates. |
| `tests/test_library_live.py` | Opt-in hardware smoke test: reads and previews only, with extra request guards; skipped by default. |
| `tools/probe.py` | GET-oriented probe; `daqOnly=true` by default (not a server-side write restriction). |
| `tools/set_parameter.py` | Guarded one-parameter numeric setter; preview by default, always `daqOnly=true`, verified read-back, no write replay. |
| `tools/re/` | Helpers for querying the Kuna decompilation export. |
| `firmware/` | Provenance and checksums for the vendor artifacts. Binaries are not tracked. |
| `LICENSE` / `LICENSING.md` | Standard Apache-2.0 terms, scope, agent-generated provenance, copyright limitations, and publication privacy. |

## Library quick start

Python 3.11+ is required. Install the library and optional test runner:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'

.venv/bin/hvctl <boiler-ip> channels
.venv/bin/hvctl <boiler-ip> info  # includes private device identifiers
.venv/bin/hvctl <boiler-ip> pcb
.venv/bin/hvctl <boiler-ip> list
.venv/bin/hvctl <boiler-ip> get 4 5
.venv/bin/hvctl <boiler-ip> set 4 21  # preview only
```

```python
from hvclient import AsyncHargassnerClient

async def read_snapshots(host):
    async with AsyncHargassnerClient(host) as client:
        channels = await client.async_get_channels()
        info = await client.async_get_info()
        pcb = await client.async_get_pcb()
        parameter = await client.async_get_parameter("4")
        return channels, info.software_version, pcb.internal_temperature, parameter.value
```

The library always sends `daqOnly=true` and exposes no mode/level override or
arbitrary endpoint tunnel. Parameter reads return USER entries plus six PLUMBER
slopes and three DEVELOPER circuit modes, including hidden/inactive entries.
Channels, info, and PCB are strictly GET-only, with typed
immutable snapshots; identity mutation and all other device routes remain blocked.
Writes are denied by default; enabling a subset of 11 USER temperatures, six
dimensionless slopes, three circuit modes, and six daily/weekly circuit programs
requires an explicit `WritePolicy`.
Curve/mode writes require an expected current value and validated circuit
configuration. Slopes require the correct active/inactive pair; mode choices
are checked against the exact hidden DEVELOPER contract and IDs 0–5, never a
whole-boiler/SERVICE mode. No other higher-level writes are enabled.
Program writes require the expected full program and matching active daily/
weekly selection. `DailySchedule`, `WeeklySchedule`, `TimeRange`, `WeeklyTimeRanges`,
and `Weekday` express comfort windows. The controller's global daily/weekly
setting D9 and circuit operating mode are never implicitly changed.
The CLI calls the same library, and applying
a write requires both `--apply` and `--expect`. See the
[library reference](library.md) for examples and safety limitations.
Circuit-mode parameters also expose reviewed English `.meaning` metadata
(`zone`, `purpose`, `label`, `description`). The public `CIRCUIT_MODE_PARAMETERS`
registry resolves controller zones `"1"`, `"2"`, and `"A"` to their API names,
so consumers need not hard-code `A1z`/`A11z`/`A61z` or parse localized labels.
`CIRCUIT_SCHEDULE_PARAMETERS["1"].daily`/`.weekly` similarly resolve heating
programs (`3`/`3a`, `6`/`6a`, `HP3`/`HP3a`), with English `comfort_schedule` meanings.
Their `.limits` and returned `Parameter.schedule_limits` expose immutable
`ScheduleLimits` for group/window counts, weekday coverage, time bounds/resolution,
overlap/overnight rules, replacement requirements, and serialization slots.
Validators use those same policy objects; live eligibility is checked separately.

Full offline suite (synthetic data and loopback HTTP servers, no boiler access):

```bash
HVCLIENT_LIVE=0 .venv/bin/python -B -m unittest discover -s tests -v
```

### Optional live smoke test

Run only against a boiler you administer, with its host explicitly supplied:

```bash
HVCLIENT_LIVE=1 HVCLIENT_LIVE_HOST=boiler.example \
  .venv/bin/python -B -m unittest tests.test_library_live -v
```

Replace `boiler.example` with the boiler's bare hostname/IP (HTTP port 80).
There is **no default address**. Without `HVCLIENT_LIVE=1`, hardware discovery
skips before creating a session; explicitly enabling it without a host fails.
Run this one test manually and serially, not alongside other live clients.
CI and pytest-xdist workers are refused before network activity.

It reads info, channels, PCB diagnostics, and supported parameters, then uses
current-value previews for representative active temperatures, slopes, and
circuit modes/programs. It discovers the selected profile rather than assuming
radiators, underfloor heating, or daily/weekly scheduling. Optional absent
controls are not previewed. It also checks refusal of the inactive program,
public limits, and unchanged selected values on a separate read. No fixed
firmware version, channel count, or installation setting is asserted.

**No parameter writes are permitted**, including no-op setter calls. A test-only
request guard checks authentication bodies for `daqOnly=true` and blocks other
POSTs; an additional transport guard pins the destination and exact method/path
allowlist. Only authentication may POST. `daqOnly=true` is **not** a server-side
read-only permission. Requests use bounded timeouts/authentication attempts and
never change global scheduling, circuit type, limits, or operating mode.

The test prints no response documents, identifiers, schedules, challenges, or
cookies, and writes no captures. Failures report the phase and exception type
without device details. Do not use traceback-local dumps (`--showlocals`),
debuggers, or HTTP debug logging when collecting output for publication.
Concurrent UI/client changes or one-time mode transitions can fail the final
comparison legitimately: inspect the controller, **do not automatically retry,
restore, or re-arm anything**. A pass validates reads/previews only, not actual
writes, physical heating effects, or reboot persistence.

## Home Assistant integration

See [the integration guide](home-assistant.md) for setup, explicit write
opt-in, program-action examples, and the separate HA test environment.
The integration imports its bundled SDK relatively. The standalone distribution
builds from that same source tree. Configuration/polling do not write. The supported calls were
curated to avoid initiating the boiler's SERVICE mode.

Integration 0.3.0 adds a climate entity for each applicable circuit with mode
writes enabled: Off/Auto/Heat plus four exact presets. The dial edits individually
opted-in persistent comfort/reduced targets, including in one-time presets;
expiry stays controller-owned. Existing numbers/selects remain available.

Public release and Marketplace catalog acceptance remain separate gates. The
public metadata uses CircuitLantern; release validators, installation checks,
and final privacy/provenance review must pass before publication.

## Research tools

These predate the library and remain investigation tools, not dependencies of
the library or the intended Home Assistant integration.

```bash
# Challenge/cooldown check (no authentication)
python3 tools/probe.py <boiler-ip> challenge

# Authenticate without requesting SERVICE mode and read the channel map
python3 tools/probe.py <boiler-ip> channels

# Standard read routes are also available without --full
python3 tools/probe.py <boiler-ip> get /info
python3 tools/probe.py <boiler-ip> get /parameters
```

The probe does not send POST/PUT/DELETE except to `/authenticate`, and blocks
known paths that mutate regardless of method. HTTP GET alone is not a read-only
guarantee; restrict `get` to documented read endpoints. `daqOnly=true` enables
`/channels` but does not restrict other API routes or writes. `--full` sets
`daqOnly=false`, which requests SERVICE mode, and requires the explicit
`--allow-service-mode` flag.

The analyzed API also has a real `POST /parameters` setter reachable with
`daqOnly=true`; it is not exposed by this GET-oriented probe. See
[`docs/boiler-http-api.md` §5.2](boiler-http-api.md#52-parameters) for the
write path, read-back requirements, and the complete USER-parameter table.
Catalog `USER` classification is not a guarantee that a setting is harmless.

For an intentional numeric change, use the separate guarded setter:

```bash
# Preview and validate a circuit-1 room target; no parameter write
python3 tools/set_parameter.py <boiler-ip> 4 21

# Apply only if the preview's current value was 20.5 and this change is intended
python3 tools/set_parameter.py <boiler-ip> 4 21 --apply --expect 20.5
```

The setter has an explicit 11-name numeric allowlist and no SERVICE-mode option
or fallback. It refuses inactive settings, validates live type/range/increment,
and sends at most one update before a separate read-back. See
[`docs/parameter-setter.md`](parameter-setter.md) for supported names,
exit codes, and the limits of verification. Example values are not recommendations.

Standalone setter tests (standard library only):
`python3 -B -m unittest discover -s tests -p test_set_parameter.py -v`.

## Firmware artifacts

The vendor update archive and the extracted firmware tree are intentionally not
tracked in git:

- `smart_hv_2.7.4.hup`
- `smart_hv_2.7.4/` (boot images, rootfs, binaries, generated decompilation)
- `smart_hv_2.7.4/data/app/bin/app.kuna/`
- `smart_hv_2.7.4/data/app/bin/app.kuna.incomplete/`

See [`firmware/README.md`](../firmware/README.md) for provenance and checksums.
Verify a local copy from the repository root with:

```bash
sha256sum -c firmware/SHA256SUMS
```

The RE helpers locate the Kuna export relative to the repository root. Set
`KUNA_DIR` to override it:

```bash
KUNA_DIR=/path/to/app.kuna python3 tools/re/dumpfn.py AuthenticationHandler
```

## Git hygiene

- `.gitignore` excludes firmware artifacts, generated decompilation, captures,
  secrets, Python caches, and editor/OS files.
- `.gitattributes` normalizes text to LF, marks binaries, and hides generated
  decompilation from diffs and language statistics.
- `.editorconfig` keeps whitespace and indentation consistent.
- `.githooks/pre-commit` blocks vendor/generated artifacts, files over 5 MiB,
  private keys, Python syntax errors, and whitespace errors.
- Keep private identifying/location information out of project text, fixtures,
  diagnostics, package metadata, and artifacts. Use relative paths and synthetic
  examples. Review Git identity metadata and archive headers before publishing;
see [publication privacy](../LICENSING.md#publication-privacy).

Enable the hooks in a fresh clone with:

```bash
git config core.hooksPath .githooks
```

Hooks are local configuration and are not propagated by Git itself.

## Safety

This is unofficial software for heating equipment, not a vendor-supported control
interface.

- Run the boiler on an isolated management network; never expose TCP/80 to an
  untrusted network. The auth salt is hard-coded in the firmware.
- Authentication with `daqOnly=false` requests SERVICE mode; that value is not
  required to access standard API routes.
- Do not call destructive endpoints (`/factory-reset`, `/update`, CAN send,
  calibration) without a documented procedure and an operator present.
- Reads are cheap; writes move actuators and can affect heating operation.

## Licence and provenance

Project-maintained code, tests, tooling, configuration, and accompanying
documentation are offered under **Apache-2.0**; see [`LICENSE`](../LICENSE) and
[`LICENSING.md`](../LICENSING.md). This is a permissive licence: modifications and
larger works may use different terms, subject to its redistribution conditions.
Preserve applicable notices, supply the licence, and mark modified files when
redistributing. There is no source-publication or upstream-submission obligation.
The full licence, including its contributor patent grant and termination terms,
governs.

The initial implementation was generated by coding agents under human
direction, with agent-executed automated checks.
This is not a claim that every line was human-authored or manually audited.
Copyrightability depends on applicable law and the actual contributions.
Directing or approving an agent does not automatically establish
copyright in its output, and a licence cannot create missing copyright or
guarantee enforceability over unprotected portions. The provenance note
explains these limitations without changing the standard Apache terms.

Vendor firmware, manuals, decompilation exports, third-party excerpts and
dependencies, and private captures are **not relicensed** by this project.
Applicable provider terms and third-party rights still need checking; no
comprehensive legal/provenance clearance review is claimed. No copyright
owner name has been invented for generated portions. The project is
unofficial and the licence is not a safety certification or vendor endorsement.

## References

- [`docs/library.md`](library.md)
- [`docs/boiler-http-api.md`](boiler-http-api.md)
- [`docs/client-development-plan.md`](client-development-plan.md)
