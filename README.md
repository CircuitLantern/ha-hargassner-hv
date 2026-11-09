# Hargassner Smart HV 2.7.4 — HTTP API analysis

Reverse-engineering notes and tooling for the HTTP service exposed on TCP/80 by
the Hargassner Smart HV 2.7.4 wood-log boiler firmware.

## Contents

| Path | Purpose |
|---|---|
| `docs/boiler-http-api.md` | Reverse-engineered API, auth, routing, and endpoint reference. |
| `docs/parameter-guide.md` | English parameter meanings, plant terminology, and a crosswalk to the German electrical/service manuals. |
| `docs/client-development-plan.md` | Independent async library, optional thin CLI, and Home Assistant integration plan; no custom GUI or data store. |
| `tools/probe.py` | GET-oriented probe; `daqOnly=true` by default (not a server-side write restriction). |
| `tools/set_parameter.py` | Guarded one-parameter numeric setter; preview by default, always `daqOnly=true`, verified read-back, no write replay. |
| `tools/re/` | Helpers for querying the Kuna decompilation export. |
| `firmware/` | Provenance and checksums for the vendor artifacts. Binaries are not tracked. |

## Quick start

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
[`docs/boiler-http-api.md` §5.2](docs/boiler-http-api.md#52-parameters) for the
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
[`docs/parameter-setter.md`](docs/parameter-setter.md) for supported names,
exit codes, and the limits of verification. Example values are not recommendations.

Offline tool tests: `python3 -B -m unittest discover -s tests -v`.

## Firmware artifacts

The vendor update archive and the extracted firmware tree are intentionally not
tracked in git:

- `smart_hv_2.7.4.hup`
- `smart_hv_2.7.4/` (boot images, rootfs, binaries, generated decompilation)
- `smart_hv_2.7.4/data/app/bin/app.kuna/`
- `smart_hv_2.7.4/data/app/bin/app.kuna.incomplete/`

See [`firmware/README.md`](firmware/README.md) for provenance and checksums.
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

Enable the hooks in a fresh clone with:

```bash
git config core.hooksPath .githooks
```

This clone is already configured. Hooks are local configuration and are not
propagated by Git itself.

## Safety

This is analysis tooling for heating equipment, not a supported control
interface.

- Run the boiler on an isolated management network; never expose TCP/80 to an
  untrusted network. The auth salt is hard-coded in the firmware.
- Authentication with `daqOnly=false` requests SERVICE mode; that value is not
  required to access standard API routes.
- Do not call destructive endpoints (`/factory-reset`, `/update`, CAN send,
  calibration) without a documented procedure and an operator present.
- Reads are cheap; writes move actuators and can affect heating operation.

## References

- [`docs/boiler-http-api.md`](docs/boiler-http-api.md)
- [`docs/client-development-plan.md`](docs/client-development-plan.md)
