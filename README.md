# Hargassner Smart HV 2.7.4 — HTTP API analysis

Reverse-engineering notes and tooling for the HTTP service exposed on TCP/80 by
the Hargassner Smart HV 2.7.4 wood-log boiler firmware.

## Contents

| Path | Purpose |
|---|---|
| `docs/boiler-http-api.md` | Reverse-engineered API, auth, routing, and endpoint reference. |
| `docs/client-development-plan.md` | Phased plan from firmware investigation to a safe client. |
| `tools/probe.py` | Read-only protocol probe; daq-only by default. |
| `tools/re/` | Helpers for querying the Kuna decompilation export. |
| `firmware/` | Provenance and checksums for the vendor artifacts. Binaries are not tracked. |

## Quick start

```bash
# Challenge/cooldown check (no authentication)
python3 tools/probe.py <boiler-ip> challenge

# Authenticate daq-only and read the channel map
python3 tools/probe.py <boiler-ip> channels

# Full API. This requests SERVICE mode on the boiler.
python3 tools/probe.py <boiler-ip> --full --allow-service-mode get /info
python3 tools/probe.py <boiler-ip> --full --allow-service-mode get /parameters
```

The probe never sends writes. Full mode requires an explicit
`--allow-service-mode` flag because a `daqOnly=false` login calls
`SystemModel::setRequestedMode(6)` on the device.

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
- A full-API login can switch the boiler to SERVICE mode.
- Do not call destructive endpoints (`/factory-reset`, `/update`, CAN send,
  calibration) without a documented procedure and an operator present.
- Reads are cheap; writes move actuators and can affect heating operation.

## References

- [`docs/boiler-http-api.md`](docs/boiler-http-api.md)
- [`docs/client-development-plan.md`](docs/client-development-plan.md)
