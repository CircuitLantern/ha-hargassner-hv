<!-- SPDX-License-Identifier: Apache-2.0 -->

# Home Assistant — Hargassner Smart HV

The **`hargassner_hv`** custom integration connects Home Assistant directly to
the boiler's LAN HTTP API through the independently packaged `hvclient` library.
It targets **Home Assistant 2026.11.1**, with a HACS-compatible repository layout.
No cloud, MQTT bridge, custom dashboard, or separate polling service is needed.

## Capabilities

| Feature | Behavior |
|---|---|
| Config flow | Host/port entry, read-only validation, stable device identity, duplicate prevention |
| Reconfiguration | Changes host/port only after verifying the same controller identity |
| Operational sensors | Boiler/return/flue/outdoor/buffer, flow/DHW/room readings, fan speed, oxygen, and reviewed calculated indications/targets |
| Setting readbacks | Parameter values and programs under Diagnostics, disabled by default for new entities |
| Number controls | 11 temperature settings and 6 slopes, individually opted in |
| Mode selects | Circuit 1/2/A: Off, Automatic, permanent reduced/comfort, one-time reduced/comfort |
| Climate controls | One per applicable, mode-opted-in circuit; Off/Auto/Heat, four presets, and individually opted-in persistent targets |
| Program action | Preview or replace one circuit's full daily/weekly comfort program |
| Additional diagnostic channels | Optional unclassified/internal readings, disabled by default |
| PCB diagnostics | Optional raw temperature/voltage readings, disabled by default |
| Diagnostics download | Whitelisted health/configuration flags, not device IDs, addresses, readings, or schedules |

**All writes are off by default.** The integration's supported calls were
curated to avoid initiating the boiler's **SERVICE mode**.
Setup, polling, saving options, reload, recovery, and unload do not write
settings. The production integration uses only the
library's transport methods and reviewed validation policy; no protocol or setter implementation is duplicated.
It cannot detect or clear a boiler-wide SERVICE condition entered by another
client, and never attempts to restore an assumed operating mode.

Climate controls are an interface to the controller's existing regulation, not
a Home Assistant thermostat algorithm. Other USER values and unreviewed
installer settings are not automatically exposed as controls. See the
[climate feature plan](climate-development-plan.md) for design and validation
boundaries. The adapter is now version **0.3.0**; the unchanged SDK remains 0.1.0.

## Installation status

The layout and local implementation are ready for development/testing, but a
normal public HACS installation is **not release-ready yet**:

1. `hargassner-hvclient==0.1.0` must be published to the package index. It is
   pinned in the integration manifest but is currently available only locally.
2. Replace the reserved `example.invalid` documentation/issue URLs with approved
   public project URLs and assign a public project owner/team in `codeowners`.
   No account or repository identity is assumed by this checkout.
3. Supply appropriately licensed brand assets required by the distribution
   channel and run its release validators. No vendor logo is bundled.
4. Validate installation on the intended HA deployment and complete controlled
   hardware checks before enabling production control automations.

`hacs.json` specifies the minimum tested HA version. Only
`custom_components/hargassner_hv/` is integration runtime code; the library
remains a separately installed/versioned requirement. It is not vendored into
that directory, and the library distributions exclude HA code and tests.

### Local development installation

For manual installation in your own HA OS/container instance, prepare a private
bundle from the current checkout (Python 3.11+):

```bash
python -m pip install build 'setuptools>=77' wheel
python tools/prepare_ha_dist.py
```

The script builds the independent SDK wheel without build isolation, stages
the integration with a local-wheel manifest, and generates:

- `dist/hargassner_hv-haos-<integration-version>/` — unpacked installation assets
- `dist/hargassner_hv-haos-<integration-version>.zip` — the same assets to extract
- `dist/hargassner_hv-haos-<integration-version>.zip.sha256` — ZIP checksum

Read the generated `INSTALL.md`. Copy the bundled `custom_components/hargassner_hv`
directory, including `wheels/`, into HA's configuration directory and restart
Core. The manifest references the wheel inside that component as seen under
Core's `/config`. Only the staged manifest is changed. No deployment, publication,
HA connection, or boiler request is performed by the script.

For repeat builds of the same integration version, use
`python tools/prepare_ha_dist.py --replace`. This replaces only that version's
script-marked directory, ZIP, and checksum; unrelated/older `dist` assets remain.
Do not install stale top-level `dist` folders from earlier manually built bundles.

**SDK changes need special care:** HA may keep an already-installed package
when its version is unchanged. Increment the SDK version and update the source
manifest pin for changed SDK runtime code, or explicitly reinstall the wheel
in Core's Python environment. Replacing a same-version wheel and restarting is
not a guaranteed SDK update. Integration-only edits can use the existing SDK.

For a local Core Python environment instead:

In the **same Python environment that runs Home Assistant**, install the SDK
from this checkout or its built wheel:

```bash
python -m pip install .
```

Copy `custom_components/hargassner_hv/` into the HA configuration directory's
`custom_components/` directory, then restart HA. During Core/venv development,
the documented HA library-development override can also be used:

```bash
hass --skip-pip-packages hargassner-hvclient -c '<config-directory>'
```

Installing on the host's Python does not install into a separate HA container
or appliance environment. Until the SDK is published, this is a developer
installation path, not a one-click HA OS/HACS installation promise. Do not
remove the manifest requirement or copy SDK source into the integration as a
workaround.

In HA, choose **Settings → Devices & services → Add integration → Hargassner
Smart HV**, then enter the bare host and HTTP port. No password is required.
The connection is plain HTTP and belongs on a trusted LAN.

## Identity, updates, and availability

Identity prefers a valid controller-reported MAC, then a system ID or serial
fallback. The selected identity is hashed and retained independently of the
host; a hostname/IP is never used as a unique ID. Missing identity prevents
setup. Reconfiguration and controls verify the configured identity instead of
silently reassigning an entry to another device. This is software identity
checking, not cryptographic device authentication.

One client is shared by all entities/coordinators for an entry, borrowing HA's
managed aiohttp session. The library serializes its HTTP operations.

| Data | Interval |
|---|---|
| Operational channels | 30 seconds while the entry is loaded; no advanced option required |
| Parameters and identity | 300 seconds |
| Optional PCB diagnostics | 60 seconds when diagnostic entities have listeners |

Initial parameter/channel reads complete before platforms are loaded. Optional
PCB failures do not block healthy main telemetry. Failed updates make the
affected entities unavailable; successful polling restores availability.
Each request has a 10-second timeout, no transport read retry, and bounded
connection recovery. Temporary connection failures are handled through normal
polling using the same curated read-only calls.

Unload removes platform listeners, shuts down coordinators, cancels in-flight
control operations, and closes the SDK client without closing HA's shared
session. A cancelled write may already have applied; it is not replayed or
rolled back. No last requested setpoint/program is restored at startup.

## Sensors and controls

The device page separates three different kinds of information:

| HA section | Contents |
|---|---|
| **Sensors** | Reviewed operational readings, with descriptive names and verified native units |
| **Controls** | Circuit climate entities, after the corresponding mode-write opt-in |
| **Configuration** | Writable numbers/selects, only after explicit per-setting write opt-in |
| **Diagnostics** | Firmware version, setting readbacks, optional unclassified raw channels, and PCB readings |

Primary sensors do **not** require the advanced diagnostic option. Recognized
channels are discovered at setup and on later updates. Temperature readings
use °C; fan speeds use rpm; reviewed oxygen/output/charge indications use %.
Real measured temperatures and fan/oxygen readings support measurement
statistics. Calculated targets/indications are labelled accordingly, without
being treated as accumulated energy or measured thermal power. For example,
boiler output indication is **percent, not kW**, and buffer charge indication
is **not a liquid level or an energy meter**.

Circuit designations `1`, `2`, and `A` are controller names, not inferred room
names. Circuit/DHW settings help choose sensible defaults for optional readings;
this is not authoritative discovery of installed sensors. A manually enabled
reading remains the controller's report, even when the circuit is unused.
No missing channel is manufactured, and disappearing/failed readings become
unavailable rather than retaining an apparently current value.

### Circuit climate controls

Version 0.3.0 adds **Circuit 1**, **Circuit 2**, and **Circuit A** climate entities
only when the circuit is applicable and its mode setting (`A1z`, `A11z`, or
`A61z`) is individually enabled in integration options. No new write permissions
are granted on upgrade. Existing number/select IDs and automations are unchanged.

| Controller mode | HA mode | Preset |
|---|---|---|
| Off | Off | None |
| Automatic | Auto | None |
| Permanent reduced | Heat | Permanent reduced (`permanent_reduced`) |
| Permanent comfort | Heat | Permanent comfort (`permanent_comfort`) |
| One-time comfort | Heat | One-time comfort (`once_comfort`) |
| One-time reduced | Heat | One-time reduced (`once_reduced`) |

- Selecting Heat from Off/Auto selects permanent comfort. While already in Heat,
  it preserves the exact preset. Named presets directly select their mode, even
  from Off. Re-selecting the same mode does not rearm a one-time override.
- Selecting Auto cancels an override. Clearing a preset (None) returns an override
  to Auto but leaves Off/Auto unchanged. Turn on means Automatic; Turn off means
  circuit Off, not boiler shutdown or a guarantee against frost-protection flow.
- **The dial changes a persistent setting**, not a temporary setpoint. Comfort
  presets edit `4`/`7`/`HP4`; reduced presets edit `5`/`8`/`HP5`. The particular
  setting must also be individually opted in and have valid current metadata.
  Off/Auto and non-writable targets have no supported temperature action.
- One-time expiry is observed from the controller; HA has no reset timer and
  never restores earlier targets. The expiry depends on comfort-program phase,
  not a fixed duration or necessarily the immediately upcoming boundary.
- Temperature actions may include an already-matching HVAC mode. A request
  requiring both a mode change and a temperature change is rejected before any
  write; issue separate intentional actions instead. HA unit conversion is
  supported; only machine-roundoff near the live increment grid is corrected.

Climate mode uses the exact `Hk1Status`/`Hk2Status`/`HkAStatus` runtime channel,
not the slower mode-setting readback. A newly verified mode write takes precedence
over polls started before its verification; the next fresh channel poll wins,
including controller-owned expiry. A missing/invalid runtime mode, failed channel
or parameter poll, changed mode contract, or absent circuit makes climate
unavailable rather than guessing from a last-command setting. This mode binding
is firmware-derived and offline-tested, not live-validated on every version.
If runtime differs from the stored command and the requested mode is already
stored, the SDK would skip an unchanged write. Climate refuses that ambiguous
transition instead of pretending it occurred or forcing a replay. Inspect the
controller and wait for coherent readbacks before retrying; an unchanged select
operation likewise does not replace reported runtime mode.

Current temperature comes only from the corresponding reported room channel;
missing/sentinel readings leave it unknown without disabling valid mode controls.
Flow temperatures are never substituted. Heat is a selected mode, not proof of
heat delivery; no `hvac_action` is inferred. Room-report freshness limitations
below still apply. Dynamic feature flags and metadata are tested against HA Core;
visual dashboard behavior has not been manually validated.

Commands share a lock with number/select/program operations and check a fresh
runtime mode after identity verification. If a mode changes while a target waits,
the action is refused, not rerouted to another setting. Expected-value checks and
read-only preflight are not an atomic cross-parameter transaction: external changes
can still race a write. No automatic replay or rollback is attempted.

### Room temperature readings

`Hk1Fr35RaumTemp`, `Hk2Fr35RaumTemp`, and `HkAFr35RaumTemp` are presented as
**Circuit 1/2/A room temperature (reported)**. Firmware definitions identify
these as °C, separately from the comfort/reduced **settings**. The reviewed
firmware uses `-1` when no room unit is selected; this is shown as unavailable,
not an actual room temperature. Zero is not treated as missing.

The flat channel response supplies no per-sensor connection/validity flag or
sample timestamp. A room unit can leave a retained reading when disconnected,
and the known `-1` sentinel is ambiguous with a genuine reading of exactly
−1 °C. Room readings therefore have no automatic statistics state class;
they do not certify sensor presence/freshness or drive a thermostat algorithm.
Optional readings initially unavailable/unconfigured are registered disabled;
enable them in Entities once the physical setup and reported values are known.

### Setting readbacks and extra diagnostics

Read-only parameter values are **setting readbacks**, not measurements. Their
entities are labelled accordingly, categorized Diagnostic, and disabled by
default for new entities. HA does not permit read-only sensor entities in
the Configuration category; opted-in number/select controls belong there.
Temperature settings use the supplied Celsius metadata; slopes remain
dimensionless. Program readbacks expose `program` and policy `limits`
attributes. Their state `active`/`inactive` means firmware applicability,
**not heating now**.

The **Make additional diagnostic channels available (advanced)** option adds
unclassified/internal channels only. These remain raw, disabled diagnostics
with no guessed unit, device class, or statistics class. PCB diagnostics also
remain optional/raw. Unknown names are not guessed to be temperatures, energy,
booleans, or decoded operating states. The [channel guide](channel-guide.md)
lists the curated keys and the evidence/limitations behind their interpretation.

### Upgrade from 0.1.x

Version **0.2.0** retains the existing channel and parameter unique IDs, entity
IDs, custom names, and explicit user enable/disable choices. Previously raw
curated readings move from Diagnostics to Sensors. When the old integration
alone disabled them, they are automatically enabled only if the new reading's
default is applicable/available and the entry permits new entities. Explicit
user/device disables and the disable-new-entities preference are not overridden.

Existing enabled setting readbacks move to Diagnostics but are not forcibly
disabled. New readbacks start disabled. Old entity IDs may still contain
`raw_`; that does not change their new name/category, and avoids breaking
dashboard references. Review numeric automations after native units are added:
HA's normal unit preferences may change the displayed unit. No setting is
written by the entity-registry migration or the upgrade.

### Opt in to writes

Open the integration's **Configure** options, select specific settings in
**Allow writes to these settings**, and acknowledge the operational risk.
Saving options reloads the integration; it does not apply any setting.

- Selected numeric settings become `number` entities with live min/max/step.
  Integer SDK parameters are sent as integers, never truncated fractions.
- Selected circuit modes become `select` entities using stable mode IDs, not
  translated controller labels. Unconfigured circuits remain unavailable.
- Selected programs become eligible for the dedicated action below; there is
  no arbitrary API-name/value service.
- Removing opt-in prevents further control calls, including through stale entity
  objects after reload. Presence in the parameter catalog is not permission.

The SDK rereads metadata, validates the configured circuit/profile, and checks
expected current values before posting one sparse change. For number/select
entities, the expected value comes from the current coordinator snapshot.
Stored state changes are shown only from verified read-back, never by assigning
the requested value optimistically. Full refreshes then reconcile the catalog.

Stale state, inactive profiles, context changes, or uncertain responses raise
HA errors. A matching later value does not turn a lost POST into a verified
write. No exception path replays, re-arms, or rolls back a setting. Inspect and
refresh before another intentional command. One-time circuit modes follow
controller schedule transitions; the integration does not implement a timer.

The integration's diagnostics payload excludes configuration dictionaries,
raw snapshots, identifiers, addresses, readings, and schedules. HA may add
its own system/environment metadata around a diagnostics download; inspect
the complete download before sharing it. Hashing device identity is not a
promise that an identifier is safe to publish. Success flags describe the last
update attempt, not a new reachability check; disabled diagnostics do not poll.

## Circuit program action

Use **`hargassner_hv.set_circuit_program`** in Developer Tools → Actions or an
automation. It is admin-only and targets **exactly one controller device**.
The chosen program must be explicitly opted in. Fields:

| Field | Meaning |
|---|---|
| `device_id` | HA device-registry ID selected in the UI; required, no default device |
| `circuit` | `"1"`, `"2"`, or `"A"` |
| `kind` | `daily` or `weekly`, already selected globally at the controller |
| `program` | Daily `{time_ranges: [...]}` or weekly `{groups: [...]}` |
| `expected` | Full current program in the same object shape; required to apply |
| `preview_only` | Defaults to `true`; set `false` only for an intentional apply |

Daily preview (syntax only; replace the device placeholder):

```yaml
action: hargassner_hv.set_circuit_program
data:
  device_id: <selected-controller-device-id>
  circuit: "1"
  kind: daily
  preview_only: true
  program:
    time_ranges:
      - start: "06:00"
        end: "09:00"
      - start: "16:00"
        end: "22:00"
response_variable: program_preview
```

The optional response contains `outcome: preview`, the normalized `requested`
array, the live `current` array, and public `limits`. No parameter POST is sent.
For an apply, also supply `expected: {time_ranges: <complete-current-array>}`
and `preview_only: false`. Copy all current windows, not only one changed
interval. The action returns verified/unchanged status when response data is
requested; failures are exceptions, not successful response codes.

A weekly replacement uses this `program` shape:

```yaml
groups:
  - weekdays: [1, 2, 3, 4, 5]
    time_ranges:
      - start: "06:00"
        end: "09:00"
      - start: "16:00"
        end: "22:00"
  - weekdays: [6, 7]
    time_ranges:
      - start: "08:00"
        end: "22:00"
```

Weekly `expected` uses the same `groups` key with the complete current program.
Days are ISO/Qt **1 Monday through 7 Sunday**; all seven must appear exactly
once across one/two groups, with at most two windows per group. Times are the
**controller's local wall clock**, at minute resolution. No HA timezone/DST
conversion is performed. `24:00` is end-only; overnight/overlapping ranges are
rejected. Empty windows mean reduced operation in Automatic, not circuit Off.

This replaces a complete program, not a partial interval patch. The SDK pads
daily slots and strips weekly unused zero-length slots. The limits are reviewed
SDK policy, not advertised hardware capabilities or a caller override. Global
`D9`, circuit type, flow limits, and operating mode are never implicitly changed.
Weekly requests are refused while daily scheduling is selected, and vice versa.

## Testing and validation boundary

The HA suite uses the actual pinned HA core, config-entry/flow managers,
coordinators, registries, entities, and services. It has separate Python 3.14
dependencies and does not add HA to the SDK's runtime or normal test environment:

```bash
uv venv --python 3.14 .venv-ha
uv pip install --python .venv-ha/bin/python -r tests/ha/requirements.txt -e .
.venv-ha/bin/python -B tests/ha/run.py -q
```

The launcher loads HA before pytest's plugins and selects only the required
async/socket/timeout plugins, avoiding ambient test-helper version mismatches.
Fixtures initialize real HA registries and a managed session with a test-only
non-discovery resolver. SDK mocks and loopback HTTP devices provide synthetic
data; non-loopback sockets are disabled. The loopback tests exercise
HA → SDK → HTTP, including all circuit climate mappings, persistent targets,
controller-owned expiry, stale-poll ordering, native-unit conversion, permission
and metadata gating, queued/cancelled controls, full program serialization, unchanged modes, lost
POST responses, strict authentication, and zero implicit global configuration
changes. No real boiler is contacted by this suite.

Default SDK tests remain independent:

```bash
HVCLIENT_LIVE=0 .venv/bin/python -B -m unittest discover -s tests -q
```

Live SDK reads/previews have been smoke-tested separately. After manual
installation, the user reported that the climate entities were tested and
appeared to work correctly so far. This is an initial live smoke-check report,
not a recorded per-command hardware validation procedure. The particular actions
tested, controller-owned expiry, persistence of target edits after expiry,
physical heating effects, and reboot persistence were not individually reported.
Opt-in controls have offline coverage and this initial user report, **not
comprehensive production hardware validation**.
No hardware request, publication, push, or setting change is implied by installing
the source or running these tests.

## Licence

Apache-2.0; see the repository's `LICENSE` and `LICENSING.md`. Third-party material
and dependencies retain their own licences. This unofficial integration is
not vendor endorsement or a safety certification.
