<!-- SPDX-License-Identifier: Apache-2.0 -->

# Independent Hargassner client library

`hargassner-hvclient` provides **`hvclient`**, an independent async Python
library for reading Hargassner boiler telemetry and changing reviewed heating
settings over the local HTTP API. It includes the thin **`hvctl`** testing CLI
and has no Home Assistant dependency. Python 3.11+ and aiohttp 3.12+ are required.

## Capabilities

| Data or control | Read | Write |
|---|---|---|
| Runtime channels: measured inputs, calculated targets, demand, actuator signals, and raw state/fault codes | Dynamic numeric snapshot | No |
| Device information: firmware version and identity fields | Typed `SystemInfo` | No |
| PCB diagnostics: internal temperature and voltage readings | Typed `PcbInfo` | No |
| 11 USER temperatures: comfort/reduced room targets, DHW targets, outdoor shutdown thresholds | Values and live limits | Explicit opt-in |
| 6 heating-curve slopes for radiator/underfloor profiles | Both variants per circuit | Explicit opt-in; active configured profile only |
| 3 circuit-mode parameters, each with 6 choices: Off, Automatic, permanent/one-time comfort or reduced | Choice IDs and labels | Explicit opt-in; configured circuit only |
| 6 daily/weekly comfort programs for circuits 1, 2, and A | Structured time windows and weekdays | Explicit opt-in; full replacement of the selected variant |
| Other USER parameters: choices, booleans, text, clock values, and other schedules | Typed immutable values | No |

Callers also get **numeric bounds/increments, choice lists, English circuit
meanings, zone-keyed control registries, and queryable schedule limits**.
Availability depends on the controller's current configuration; a supported
parameter name does not establish that its circuit or optional hardware exists.

**Writes are disabled by default.** `WritePolicy.only(...)` enables a subset of
the 26 reviewed write names, never arbitrary installer/developer access.
Writes validate current metadata and context, submit one parameter, and verify
the response plus a separate read-back. Uncertain writes are never replayed.
Authentication always uses **`daqOnly=true`**; no operation requests SERVICE mode
or implicitly changes global daily/weekly scheduling, circuit type, or limits.

- [Telemetry and device reads](#read-channels-device-info-and-board-diagnostics)
- [Parameter values and metadata](#read-parameters)
- [Temperature, curve, mode, and program writes](#explicitly-permitted-writes)
- [Queryable schedule limits](#queryable-schedule-limits)
- [Sessions and lifecycle](#sessions-and-lifecycle)
- [CLI](#thin-testing-cli)
- [Compatibility and tests](#verification-and-scope)

## Install

From a source checkout or unpacked source distribution:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
```

Dependencies are aiohttp and yarl. The package imports neither the research
tools nor Home Assistant. It has no persistent storage, polling daemon, or
background tasks; applications decide when to call it.

The canonical SDK sources live in
`custom_components/hargassner_hv/hvclient/`. Standalone packaging builds that
same tree as `hvclient`; it does not package or import the HA adapter. The
integration imports the SDK through its own relative namespace, so Marketplace
installation needs no standalone SDK install or PyPI SDK publication.

## Quick start

```python
import asyncio
from hvclient import AsyncHargassnerClient

async def main(host):
    async with AsyncHargassnerClient(host) as client:
        channels = await client.async_get_channels()
        daytime = await client.async_get_parameter("4")
        info = await client.async_get_info()
        pcb = await client.async_get_pcb()
        print(channels.get("aussentemperatur"))  # raw channel value, if present
        print(daytime.value, daytime.unit, daytime.minimum, daytime.maximum)
        print(info.software_version)
        print(pcb.internal_temperature, pcb.internal_voltage)

asyncio.run(main("boiler.example"))  # replace with the boiler's host
```

The host is a bare hostname/IP, not a URL; pass the port separately if needed.
This example only reads. The context manager handles authentication and cleanup.

## API at a glance

| Async method | Result |
|---|---|
| `async_get_channels()` | `ChannelSnapshot`: channel name → finite number |
| `async_get_info()` | `SystemInfo`: firmware/version and optional identity strings |
| `async_get_pcb()` | `PcbInfo`: two board diagnostic readings |
| `async_get_parameters(names=None)` | `dict[str, Parameter]`; all supported entries or a selected subset |
| `async_get_parameter(name)` | One `Parameter`, including its value and metadata |
| `async_preview_parameter_write(name, value, expected=...)` | `WritePreview`; validates without a parameter POST |
| `async_set_parameter(name, value, expected=...)` | `WriteResult`; unchanged or verified, otherwise a typed exception |
| `async_close()` | Closes the client without changing controller state |

## Read channels, device info, and board diagnostics

All three methods use GET with no body and share the same authentication,
cookie refresh, operation lock, timeouts, response-size bound, and bounded read
recovery as parameter reads. They work with the default read-only write policy.
They do not load the parameter catalog or enable any writes.

| Method | Return type | Data |
|---|---|---|
| `async_get_channels()` | `ChannelSnapshot`, an immutable numeric `Mapping` | Dynamic channel names and finite numbers, including zero and integer-valued states/counters |
| `async_get_info()` | `SystemInfo` | `software_version`, `mac`, `system_id`, `commission_number`, `article_serial_number` |
| `async_get_pcb()` | `PcbInfo` | `internal_temperature` and `internal_voltage`, both finite numbers |

### Device information: identity and installed software (`/info`)

This describes **which controller answered and which application version it
reports**, rather than its current heating performance or configuration catalog.

| Python attribute | JSON field | Meaning |
|---|---|---|
| `software_version` | `softwareVersion` | Boiler application/firmware version string reported by the controller; useful when recording compatibility observations |
| `mac` | `mac` | Reported network-interface MAC address; not the controller's IP address or a heating measurement |
| `system_id` | `systemId` | Firmware-provided system identifier; distinct from the MAC address and serial-number fields |
| `commission_number` | `commissionNumber` | Configured manufacturer commission/order or installation reference; not a commissioning date or operating-hours counter |
| `article_serial_number` | `articleSerialNumber` | Reported article/unit serial-number field; not the PCB serial number or hardware revision |

Known fields accept strings, missing fields, or null. Missing/null fields remain
`None`; no replacement identity is generated. Unknown response fields remain
available in immutable `.raw` metadata. Do not assume that identifiers are
immutable across controller replacement, reprovisioning, or configuration/reset
operations; identity persistence still needs deployment-specific validation.
The library never repairs missing identity by writing/generating a new one.

These fields are useful for identifying a device and recording its reported
software version. They are **not** a decoded operating mode, an alarm history,
a capability-negotiation result, or proof that a firmware-specific write works.

**Privacy:** `/info` contains private device identifiers. Known identity fields
and raw extensions are hidden from `SystemInfo`'s representation, but explicit
`.to_dict()` and CLI `info` output contain the original identifiers. Sanitize
them before publishing logs or Home Assistant diagnostics.

### Channels: runtime observations and control signals (`/channels`)

This returns the controller's **currently published numeric channel values**.
They are not all sensor measurements: the same map can contain measured inputs,
calculated targets, requested outputs, state/fault codes, and debug/resource
signals. It is distinct from the parameter catalog and does not provide writes.

Examples from the locally captured 3.4.0 channel names are grouped below. The
English descriptions summarize names/manual context, **not a validated decoder
for every channel's scale, units, state codes, or control algorithm**.

| Information category | Example channel names | What the signals concern |
|---|---|---|
| Boiler and buffer temperature inputs | `kesselfuehler`, `ruecklauffuehler`, `rauchgasfuehler_nicrni`, `puffer_oben`, `puffer_mitte`, `puffer_unten` | Boiler-water, return-water, flue-gas, and thermal-store top/middle/bottom temperature-related readings |
| Outdoor and room observations | `aussentemperatur`, `AussentempMittel`, `Hk1Fr35RaumTemp`, `Hk2Fr35RaumTemp`, `HkAFr35RaumTemp` | Outdoor temperature, its controller average, and room-unit temperature-related readings for circuits 1/2/A |
| Targets and heat demand | `TKsoll`, `RL_Soll`, `Hk1Soll`, `HeizkreisAnforderung`, `BoilerAnforderung`, `AnforderungFremdwaerme` | Boiler/return/circuit target-related signals and demand from space heating, DHW, or an external heat source; these are not interchangeable with measured temperatures or parameter names |
| Buffer/output indications | `Pufferfuellgrad`, `Leistung`, `Leistungsvorgabe` | Buffer-charge and boiler-output-related indications/requests; do not interpret them as a measured liquid level, instantaneous kW, or accumulated energy without validating the scale/algorithm |
| Actuator-related signals | `Hk1PumpeSoll`, `Boiler1PumpeSoll`, `SzSollRPM`, `saugzug_rpm`, `primaerluftklappe_pos`, `sekundaerluftklappe_pos` | Pump requests, fan target/speed-related values, and air-flap position-related values; a request is not confirmation of physical operation |
| Combustion, state, and fault signals | `lambda_O2`, `O2Soll`, `KesselZustand`, `Hk1Status`, `Hk1Zustand`, `Boiler1Status`, `Stoerung`, `Wahlschalter` | Oxygen feedback/target-related values and subsystem state/status, fault, or selector codes. The SDK returns raw numbers; HA has a [firmware-evidenced `KesselZustand` presentation map](channel-guide.md#boiler-state-diagnostic), not a general subsystem-state or whole-boiler system-mode decoder |
| Module and controller diagnostics | `hka_connected_channel`, `hkm_connected_channel`, `fs_space_remaining`, `sys_mem_used`, `sys_mem_available`, `temperatur_platine`, `spannung_platine` | Expansion-module connection indications, filesystem/memory diagnostics, and board-monitor signals; not a complete hardware inventory or a validated overall-health assessment |

Use `channels[name]`, `.get()`, `.items()`, or `.to_dict()`. Names are discovered
from each response, not a fixed model field list. Names and counts can vary with
firmware, configuration, and subsequent reads. Presence of an optional
subsystem's channel alone does not establish that its hardware is installed or
its reading is valid. An empty map is a valid empty snapshot.

The library preserves finite int/float values, including zero and integer-valued
states/counters. It rejects JSON booleans, strings, nulls, and non-finite values
rather than guessing conversions. It does not interpret unavailable/sentinel
values, map numeric codes to modes, or certify sensor validity. In particular,
do not assume that a `Soll`/request signal is measured feedback or that a
channel name is the HTTP parameter identifier for a related setting.

There is no documented per-channel unit, enum, validity flag, or sampling
timestamp in this flat response format. The library does **not** infer these
from names or values. It provides one returned map per call, not a subscription
or stored history, and does not guarantee that all values were sampled together.
Channels have no parameter-catalog USER/security-level classification and are
not filtered by it. **Parameter** reads are limited to USER entries plus the
six PLUMBER heating-curve and three DEVELOPER circuit-mode exceptions below.

### Board diagnostics: controller-electronics readings (`/pcb`)

This is a small diagnostic snapshot of the **controller electronics**, not a
complete boiler-health report or PCB inventory.

| Python attribute | JSON field | Meaning |
|---|---|---|
| `internal_temperature` | `internalTemperature` | Controller-reported internal/board temperature diagnostic; not boiler-water, room, DHW-cylinder, or flue-gas temperature |
| `internal_voltage` | `internalVoltage` | Controller-reported internal voltage-monitor diagnostic; not household mains voltage, electrical power consumption, or an energy counter |

Both readings must be present and finite numbers. The model never substitutes
zero for a missing reading; an actual reported zero is retained. The response
does not supply unit/calibration metadata, sensor/rail selection, normal ranges,
alarm thresholds, or a derived healthy/faulted state. Validate those meanings
against the installed controller before adding unit conversions or alerts.
Similarly named channels such as `temperatur_platine` and `spannung_platine`
are **not automatically treated as aliases** of these fields.

PCB serial number, revision, production week/year, and other EEPROM/hardware
inventory data belong to other device endpoints and are not exposed by this
method or the current library. Unknown `/pcb` extensions are preserved in `.raw`.

### Shared read behavior and safety

Models and nested metadata are immutable. Every `.to_dict()` returns a detached
wire-shaped copy; an earlier snapshot does not mutate as later reads occur.
Separate endpoint calls are not an atomic combined plant snapshot. Polling and
history, if desired, belong to the caller or Home Assistant, not the library.

The response shapes were reviewed against 2.7.4 firmware and 3.4.0 responses.
Channel examples are observational, not a fixed firmware contract.
A well-formed number or version string alone does not establish
sensor calibration, hardware presence, safe operation, or firmware compatibility.

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
        parameters = await client.async_get_parameters()  # USER + reviewed curve/mode exceptions
        daytime = await client.async_get_parameter("4")
        selected = await client.async_get_parameters(["4", "5"])
        print(daytime.value, daytime.unit)
        print(daytime.to_dict())  # detached wire-shaped metadata/value copy

asyncio.run(main())
```

Names are API names, not menu labels, channel names, or translated descriptions.
The server has no level-filtered catalog endpoint: a full GET receives the
catalog, then discards unsupported rows locally. USER entries, six named PLUMBER
curve slopes, and three named DEVELOPER modes are returned, including hidden/inactive entries. Selecting
any other non-USER name raises `ParameterAccessError`; missing selected names
raise `ParameterNotFoundError`. `Parameter.security_level` preserves the actual
`USER`, `PLUMBER`, or `DEVELOPER` classification; exceptions are not relabeled USER. There is no
general INSTALLER/PLUMBER/SERVICE/DEVELOPER option. Nested catalog `children`
are not exposed through parameter metadata.

`Parameter` and its values/metadata are immutable. Scalar types are ordinary
Python int/float/bool/str. Other USER values are:

- `Choice`, or tuples of `Choice` for multiple-choice values; IDs and labels
  are separate, and the available choices are in `Parameter.choices`.
- Tuples of `TimeRange` for daily schedules. Strings such as `24:00:00` and
  zero-length slots are preserved, not converted to `datetime.time`.
- Tuples of `WeeklyTimeRanges`, preserving numeric weekday groups without
  changing their representation. The reviewed weekday map is ISO/Qt:
  1=Monday through 7=Sunday; `Weekday` exposes those IDs for writing.
- `ControllerDateTime`, preserving raw integer components, including invalid/
  unset zero and −1 components. No timezone or replacement date is assumed.
- Unknown future USER types remain read-only immutable JSON containers.

Live numeric limits, increments, active flags, boolean labels, help text, and
unknown metadata are preserved. `active` is not current heating state or HTTP
permission. For the three mode exceptions, false deliberately means UI-hidden,
not an absent circuit; the mode policy checks configured circuit presence
separately. USER is not a network permission or safety guarantee.

| `Parameter` attribute | Available information |
|---|---|
| `.value`, `.type`, `.unit` | Decoded value, firmware type, and supplied unit |
| `.minimum`, `.maximum`, `.increment` | Live numeric acceptance bounds and step, when supplied |
| `.choices`, `.true_text`, `.false_text` | Choice IDs/localized labels and boolean display labels |
| `.active`, `.security_level` | Firmware applicability/visibility and catalog classification |
| `.meaning` | Reviewed English zone/purpose/label/description for circuit modes and programs; otherwise `None` |
| `.schedule_limits` | Static SDK limits for the six circuit programs; otherwise `None` |
| `.to_dict()` | Detached wire-shaped data, without library meanings or limits added |

## Explicitly permitted writes

Writes are **denied by default**. Only the 11 USER temperatures, six dimensionless
slopes, three circuit-mode choices, and six USER circuit programs below can be enabled.
The caller chooses a subset with
`WritePolicy.only(...)`; it cannot opt into arbitrary names.

### Preview and apply a permitted USER temperature change

```python
from hvclient import AsyncHargassnerClient, WritePolicy

async def intentional_change(host, requested, expected):
    async with AsyncHargassnerClient(host, write_policy=WritePolicy.only("4")) as client:
        preview = await client.async_preview_parameter_write("4", requested, expected=expected)
        # Inspect/confirm before applying. Preview never sends a parameter POST.
        result = await client.async_set_parameter("4", requested, expected=expected)
        return result
```

Both preview and set read current metadata and validate the proposed value.
Numeric writes require an active parameter, the reviewed type/unit, finite
values, live bounds, and an exact minimum-anchored increment grid; values are
not rounded or clamped. INTEGER settings require int, not bool or float.
Circuit modes and programs have the additional context checks described below.

| Write category | `expected=` for actual writes |
|---|---|
| USER temperatures | Optional in Python; required by the CLI |
| Heating-curve slopes | Required current slope |
| Circuit modes | Required current integer/enum mode ID |
| Circuit programs | Required current full program, e.g. `current.value` |

`expected` is a stale-value check, not atomic compare-and-set. Previews remain
available without it. Unchanged values return `outcome="unchanged"` without a
POST. A change sends one sparse `name`/`value` entry, checks the returned object,
and performs a separate GET. Success returns `WriteResult` with
`.before`, `.returned`, `.after`, `.requested`, `.outcome`, and `.write_status`;
`.verified` and `.read_back_matches` summarize the result. Physical effects and
reboot persistence are not established by stored-value verification.

### Meaning of the USER temperature parameters

All values below are in **°C**. Use the **API name** in library calls; the menu
label is included only to identify the corresponding controller setting.
"Zone" means a space-heating circuit, not necessarily a particular room.

| API name | Controller menu | Type | English meaning |
|---|---|---|---|
| `4` | Hk1-2 | FLOAT | Zone 1 daytime/comfort room-temperature target |
| `5` | Hk1-3 | FLOAT | Zone 1 reduced/setback room-temperature target |
| `7` | Hk2-2 | FLOAT | Zone 2 daytime/comfort room-temperature target |
| `8` | Hk2-3 | FLOAT | Zone 2 reduced/setback room-temperature target |
| `HP4` | HkA-2 | FLOAT | Zone A daytime/comfort room-temperature target |
| `HP5` | HkA-3 | FLOAT | Zone A reduced/setback room-temperature target |
| `2` | B1-2 | INTEGER | Domestic-hot-water cylinder 1 target water temperature |
| `HP2` | BA-2 | INTEGER | Domestic-hot-water cylinder A target water temperature |
| `11` | Hk-11 | INTEGER | Outdoor-temperature shutdown threshold for all heating circuits, independently of heating windows |
| `12` | Hk-12 | INTEGER | Outdoor-temperature shutdown threshold for all heating circuits during day setback |
| `13` | Hk-13 | INTEGER | Outdoor-temperature shutdown threshold for all heating circuits during night setback |

**Room targets (`4`, `5`, `7`, `8`, `HP4`, `HP5`).** These are desired room
temperatures, not measured room temperatures or heating-water flow targets.
The daytime/comfort target applies during the circuit's comfort-heating
windows; the reduced target applies during setback. Their actual effect depends
on the heating curve, weather compensation, room-sensor configuration, operating
state, and other controller conditions. Changing a target is not a direct
pump/valve command and does not guarantee immediate heating or a particular
measured room temperature. Setback is not necessarily "off".

**DHW targets (`2`, `HP2`).** These concern the cylinder supplying domestic hot
water, **not the wood-fired boiler's own water temperature**. In the German
manual, "Boiler" means the DHW cylinder; "Kessel" means the wood-fired boiler.
The target alone does not determine when charging runs: schedules, reheat
differentials, minimum-temperature triggers, heat availability, DHW priority,
and hygiene cycles also influence the controller. Software acceptance ranges
are not recommendations for hygienic or scald-safe water temperatures.

**Outdoor shutdown thresholds (`11`, `12`, `13`).** These compare against the
controller's **averaged outdoor temperature**, not necessarily its instantaneous
outdoor reading. Above the applicable threshold the controller can stop space
heating: `11` applies independently of heating windows, while `12` and `13`
apply during day and night setback respectively. The controller's configured
day/night setback periods decide which of those two applies; they are not the
same as a zone's comfort-heating schedule. These are not room targets, boiler
safety limits, or unconditional pump-off controls. Frost/overtemperature
protection and other overrides can still apply.

The `A` suffix denotes the controller's additional circuit/cylinder, not
"automatic" mode. Match 1/2/A to the actual installation; catalog presence does
not establish that optional hardware is fitted. The library refuses inactive
writes and obtains limits/increments from current metadata rather than treating
any captured range as a commissioning recommendation.

These English meanings follow the Smart-HV 17–23 service manual, DE V03,
03/2021, pp. 3–5 and 8, cross-matched to the API catalog. They describe the
settings, not recommended temperatures or guaranteed behavior on every firmware.

### Heating-curve slopes: narrow installer exceptions

Each heating circuit has a floating-point **heating-curve slope** (German
"Steilheit"). It influences the circuit's requested flow/supply temperature
(Vorlauf, i.e. water entering the emitters) as outdoor temperature changes.
It is **not** a room target, measured inlet temperature, boiler return target,
or an arbitrary user-defined curve. The control calculation also uses averaged
outdoor temperature, room-target/correction inputs, configured flow-temperature
limits, and other control conditions. A larger slope generally requests hotter
supply water in cold weather; it is not simply a fixed °C offset.

There are **two alternative parameter objects per circuit**, not two slopes to
apply together. They are `PLUMBER` parameters with type `FLOAT` and an empty
unit string (dimensionless), not USER temperatures:

| API name | Circuit | Profile | Required circuit-type choice |
|---|---|---|---|
| `A2` | 1 | Radiator | A1 ID 1 |
| `A2a` | 1 | Underfloor | A1 ID 2 |
| `A12` | 2 | Radiator | A11 ID 1 |
| `A12a` | 2 | Underfloor | A11 ID 2 |
| `A62` | A | Radiator | A61 ID 1 |
| `A62a` | A | Underfloor | A61 ID 2 |

The paired objects share menu labels A2/A12/A62. In the captured metadata,
zone 2's translated help labels are reversed, so **do not use help text or a
menu label to choose a write name**. The mapping above follows the 2.7.4 control
initialization and agrees with the captured circuit-choice IDs and active flags.
The captured acceptance range was 0.1–3.5 with increment 0.1, but the library
uses current metadata, not those captured values as recommended settings.

Guarded access is explicit and deliberately narrower than installer support:

- Enable the actual slope name, e.g. `WritePolicy.only("A2a")`. Other installer
  settings, including circuit types, mixing-valve configuration, and minimum/
  maximum flow-temperature limits, remain unwriteable through the library.
- Preview/read-back fetch the chosen slope, its alternative, and its circuit
  selector in one selected GET. The chosen slope must be active, its peer
  inactive, and the selector's ID must match the requested profile. Missing,
  absent, ambiguous, or changed profile data cause refusal/failed verification.
- The selector is read **only as internal validation context**; it is not
  exposed by general parameter reads and is never included in the POST.
- Require `PLUMBER`, `FLOAT`, dimensionless unit, finite values, and valid live
  bounds/increment. Only one sparse slope name/value entry is submitted.
- Actual slope writes also **require `expected=`**, not just an opt-in policy.
  Preview remains possible without an expected value. Unchanged values skip POST.
- Verify the returned value and a fresh full profile-context read after POST.
  A profile change or lost response leaves the outcome unverified; never replay
  or automatically restore anything. Authentication always remains `daqOnly=true`.

```python
from hvclient import AsyncHargassnerClient, WritePolicy

async def intentional_slope_change(host, requested, expected):
    async with AsyncHargassnerClient(host, write_policy=WritePolicy.only("A2a")) as client:
        preview = await client.async_preview_parameter_write("A2a", requested)
        # Inspect/confirm the active profile and change before explicitly applying.
        return await client.async_set_parameter("A2a", requested, expected=expected)
```

For inspection, `hvctl <boiler-ip> get A2 A2a` reads both variants; `set` previews
by default and applying still requires `--apply --expect <current>`. There is
no automatic profile switching or general PLUMBER browsing flag.

**Safety boundary:** guarded protocol support is not proof that a particular
slope is appropriate for the installation. It can change flow temperatures and
comfort promptly, especially for underfloor heating. Keep independently
commissioned temperature limits/protections in place; this library neither
validates their suitability nor changes them. Context/expected checks are not
atomic against another client or local UI changes. No curve has been live-written
with this library; installation effects, profile behavior on other firmware,
and persistence remain **VERIFY**. No recommended slope or tuning algorithm is
supplied.

`NUMERIC_USER_WRITES` remains the original 11-name USER subset;
`HEATING_CURVE_WRITES` contains these six exceptions and `NUMERIC_WRITES` is
their 17-name union. A caller still needs an explicit per-name write policy.

### Circuit operating modes: narrow hidden-choice exceptions

The mode parameters are **`A1z` (circuit 1), `A11z` (circuit 2), and `A61z`
(circuit A)**. They are `CHOICE` parameters classified `DEVELOPER`, with the
following IDs exposed by the `HeatingCircuitMode` enum:

| ID | Enum member | Meaning |
|---|---|---|
| 0 | `OFF` | Normal circuit heating off; frost protection, residual-heat use, and other protection functions can still apply |
| 1 | `AUTOMATIC` | Follow the circuit's normal comfort/setback schedule and associated controller conditions |
| 2 | `PERMANENT_REDUCED` | Reduced/setback room target instead of scheduled comfort operation |
| 3 | `PERMANENT_COMFORT` | Daytime/comfort room target instead of scheduled setback operation |
| 4 | `ONCE_COMFORT` | One-time comfort override (1x Heizen) |
| 5 | `ONCE_REDUCED` | One-time reduced override / short absence (1x Absenken) |

#### Structured English parameter meanings

Callers do not need to hard-code the vendor's mode parameter identifiers.
The public, immutable `CIRCUIT_MODE_PARAMETERS` registry is keyed by controller
circuit label **`"1"`, `"2"`, or `"A"`** and returns a `ParameterMeaning`:

```python
from hvclient import CIRCUIT_MODE_PARAMETERS, get_parameter_meaning

definition = CIRCUIT_MODE_PARAMETERS["1"]
assert definition.name == "A1z"              # wire identifier, resolved by zone
assert definition.zone == "1"
assert definition.purpose == "operating_mode" # stable machine-readable purpose
assert definition.label == "Heating circuit 1 operating mode"
assert get_parameter_meaning("A1z") is definition  # offline lookup by wire name
```

Every returned mode `Parameter` exposes the same descriptor as **`.meaning`**,
including parameters inside previews and write results. The descriptor's
`label` and `description` are reviewed **English library metadata**, separate
from the controller's localized `Parameter.description`, `help_text`, and
choice labels. Those original fields are not translated or overwritten.

```python
from hvclient import AsyncHargassnerClient, CIRCUIT_MODE_PARAMETERS

async def identify_zone_mode(host, zone="1"):
    definition = CIRCUIT_MODE_PARAMETERS[zone]
    async with AsyncHargassnerClient(host) as client:
        parameter = await client.async_get_parameter(definition.name)
        assert parameter.meaning is not None
        return parameter.meaning.zone, parameter.meaning.purpose, parameter.meaning.label
```

The same `definition.name` can be used in `WritePolicy.only(...)` and the
ordinary preview/set methods; permissions and all mode-write checks are
unchanged. Registry lookups do not contact the boiler. A descriptor does not
prove circuit presence, establish a physical room name, or grant write access.

The three mode parameters and six daily/weekly programs have structured meanings. Unknown or
other unmapped parameter names return `None` from `get_parameter_meaning(...)`
and `Parameter.meaning`; no interpretation is guessed from a name prefix.
`Parameter.to_dict()` remains a detached **wire-shaped** copy: it does not
inject library descriptors into controller JSON. Read `.meaning` explicitly
when presenting English labels or structured circuit/purpose information.

These are **circuit modes, not boiler-wide operating modes**. There is no
SERVICE value in this enum; ID 6 and unrelated enum classes are rejected locally.
`A1`/`A11`/`A61` without `z` are circuit-type selectors, not operating-mode
parameters, and remain unwritable through the library.

The 2.7.4 state machine makes the one-time overrides schedule-transition driven,
not arbitrary-duration timers. One-time comfort returns to Automatic at a
comfort start after a setback phase has been observed; one-time reduced returns
at a setback start after a comfort phase has been observed. Without the relevant
schedule transitions an override can last longer than its UI label might imply.
The SDK does not implement a timer, schedule restoration, or automatic re-arming.

**Why `active:false` is allowed here:** all three are explicitly assigned
`AlwaysInactiveCondition` in the analyzed code. This hides these internal mode
objects from ordinary parameter UI lists; their value-change callbacks still
call `HeizkreisBase::setHKModus`. The library makes an exception only for these
exact names with `DEVELOPER`, `CHOICE`, `active:false`, no numeric unit, a valid
current choice, and exactly the six unique choice IDs 0–5. It does not expose a
general inactive/DEVELOPER-write bypass. Changed/missing metadata are refused;
translated choice labels are display text, never write identifiers.

Each preview reads the mode and its circuit-type selector in one selected GET.
The selector must be PLUMBER/CHOICE, active, and configured as radiator (ID 1)
or underfloor (ID 2), not absent (ID 0). It is internal read-only context, not a
public selector parameter. `WritePreview.circuit_type_id` records the validated
type; the CLI includes it in mode previews. Post-write verification rechecks
that the circuit type is unchanged as well as comparing the choice IDs.

```python
from hvclient import AsyncHargassnerClient, Choice, HeatingCircuitMode, WritePolicy

async def intentional_mode_change(host):
    async with AsyncHargassnerClient(host, write_policy=WritePolicy.only("A1z")) as client:
        current = await client.async_get_parameter("A1z")
        assert isinstance(current.value, Choice)
        preview = await client.async_preview_parameter_write(
            "A1z", HeatingCircuitMode.AUTOMATIC, expected=current.value.id
        )
        # Inspect/confirm before applying. Circuit-mode changes require expected=.
        return await client.async_set_parameter(
            "A1z", HeatingCircuitMode.AUTOMATIC, expected=current.value.id
        )
```

Both requested and expected modes must be integer IDs 0–5 or members of
`HeatingCircuitMode`: not booleans, floats, translated labels, `Choice` objects,
or wire dictionaries. The serializer sends only the ID object, not the label:

```json
{"parameters":[{"name":"A1z","value":{"id":1}}]}
```

`expected=` is mandatory for actual mode writes. An unchanged ID skips POST;
selecting a one-time mode already in effect does not restart/re-arm it. A POST
is sent at most once and must agree with a separate context/choice read-back.
If the controller returns an override to Automatic, a room unit changes it,
or the response is lost, the SDK reports mismatch/unknown rather than forcing
the requested mode again. Check `.result` before deciding on another intentional
change. Authentication and any read recovery remain explicitly `daqOnly=true`.

The same methods are available through the sole thin client:

```bash
hvctl <boiler-ip> get A1z A11z A61z
hvctl <boiler-ip> set A1z 1                       # preview only
hvctl <boiler-ip> set A1z 1 --apply --expect 3    # only if 3 is the reviewed current ID
```

These numbers illustrate syntax, not an instruction to change a live unit.
Circuit mode can change heat demand and may be affected by the local UI, room
units, the plant's state, and protection logic. Stored choice read-back does
not prove pump/valve behavior or that the whole boiler is in normal mode. The
checks are not atomic against other clients. No live mode write is recorded.

In the reviewed firmware, `Hk1Status`/`Hk2Status`/`HkAStatus` carry circuit-mode
IDs; `Hk*Zustand` uses a different runtime state enumeration. Channel reads
still return raw numbers, not automatic enum conversion or an authoritative
whole-boiler mode decoder.

`CIRCUIT_MODE_WRITES` lists the three mode exceptions. With the six circuit
programs below, `PARAMETER_WRITES` is the complete 26-name union; explicit
opt-in still applies.

### Automatic comfort programs: daily and weekly schedules

Automatic mode normally selects the daytime/comfort room target inside these
windows and the reduced target outside them. A window is a **comfort permission**,
not a guarantee that a pump runs or the circuit heats: outdoor cutoffs, available
heat, protection, and other controller conditions still apply. The circuit's
one-time overrides also use schedule transitions. Editing a program does not
switch the circuit to Automatic or change its temperature targets.

| Circuit | Daily API name | Weekly API name | Controller menu |
|---|---|---|---|
| 1 | `3` | `3a` | Hk1-1 |
| 2 | `6` | `6a` | Hk2-1 |
| A | `HP3` | `HP3a` | HkA-1 |

All six are USER parameters. Daily values are `DAILY_CLOCK_RANGE`: the same
from/to windows every day. Weekly values are `WEEKLY_CLOCK_RANGE`: weekday
groups with their own windows. The wire fields corresponding to "from/to" are
**`start` and `end`**. `CIRCUIT_SCHEDULE_PARAMETERS["1"].daily`/`.weekly` expose
the names, kind, zone, peer and read-only circuit-selector name without callers
hard-coding vendor identifiers. `Parameter.meaning` describes them in English
with purpose `comfort_schedule`, separately from localized firmware text.

#### Queryable schedule limits

Callers can inspect the immutable **`ScheduleLimits`** object before building
an editor or program. It is the same policy object used by time/window/day
validation, serializers, and live slot/group-count checks—not a separate copy
of documented rules:

```python
from hvclient import CIRCUIT_SCHEDULE_PARAMETERS, get_schedule_limits

program = CIRCUIT_SCHEDULE_PARAMETERS["1"].weekly
limits = program.limits
assert get_schedule_limits(program.name) is limits  # offline lookup
assert limits.max_day_groups == 2
assert limits.max_windows_per_day == 2
assert limits.time_resolution_minutes == 1
assert limits.require_all_weekdays
assert not limits.allow_overnight
```

The same object is available as **`Parameter.schedule_limits`** on reads,
previews, and write results, and as `.limits` on `DailySchedule`/
`WeeklySchedule` values. Public `DAILY_SCHEDULE_LIMITS` and
`WEEKLY_SCHEDULE_LIMITS` provide direct access without any device request.
`get_schedule_limits(name)` returns `None` for unmapped/non-circuit parameters,
including otherwise readable DHW/FWS schedules; their write support is not
inferred from a matching wire type. No limits are injected into `to_dict()`.

| Attribute | Daily | Weekly |
|---|---|---|
| `kind` | `daily` | `weekly` |
| `min_windows_per_day` / `max_windows_per_day` | 0 / 2 | 0 / 2 per day group |
| `supports_weekdays` | false; same program every day | true |
| `min_day_groups` / `max_day_groups` | `None` / `None` (not applicable) | 1 / 2 |
| `weekdays` | ISO/Qt IDs 1–7; not independently selectable | ISO/Qt IDs 1–7, exposed as `Weekday` members |
| `time_resolution_minutes` | 1 | 1 |
| `min_start_time` / `max_start_time` | 00:00:00 / 23:59:00 | 00:00:00 / 23:59:00 |
| `min_end_time` / `max_end_time` | 00:01:00 / 24:00:00 | 00:01:00 / 24:00:00 |
| `allow_end_of_day` | true, for end only | true, for end only |
| `allow_overnight` / `allow_overlaps` | false / false | false / false |
| `require_all_weekdays` | false (no weekday groups) | true |
| `require_unique_weekdays` | not applicable to daily values | true across all groups |
| `full_replacement` / `expected_required` | true / true | true / true |
| `daily_wire_slots` | 2; serialized unused slots are padded | `None`; no fixed daily padding |
| `unused_slot` | recognized 00:00:00→00:00:00 padding | recognized/omitted read padding, not a real window |
| `clock_basis` | `controller_local` | `controller_local` |

Time bounds describe **real windows**, which still need start < end. The zero-
length `unused_slot` is a serialization/read-padding marker, not a window with
an end below `min_end_time`. Weekly group minimums and full-day coverage apply
to replacements, not unused/empty groups preserved in raw controller reads.

`scope="reviewed_sdk_write_policy"` deliberately distinguishes these limits
from firmware-advertised capabilities or proof of a safe/compatible installation.
They are read-only metadata, **not caller configuration overrides**. Live
eligibility remains separate: current metadata, configured circuit, active
program pair, global D9 selection, expected-value checks, and post-write
verification must still pass. Having a limits object does not grant writes or
switch an inactive program into use.

**Global selection matters:** PLUMBER parameter **D9** (menu E14) selects daily
(`false`) or weekly (`true`) scheduling globally, including other heating/DHW
programs. The SDK reads it only as internal validation context and **never
writes or automatically switches it**. A weekly program can be written only
when the controller is already configured for weekly programs, and daily only
in daily mode. Set the global preference
deliberately at the controller if weekly operation is wanted; this is not a
per-zone toggle. D5/E12's day-versus-night setback classification is a different
setting and is not changed either.

```python
from hvclient import (
    AsyncHargassnerClient, CIRCUIT_SCHEDULE_PARAMETERS, TimeRange,
    WeeklySchedule, WeeklyTimeRanges, Weekday, WritePolicy,
)

async def replace_zone1_weekly_program(host):
    program = CIRCUIT_SCHEDULE_PARAMETERS["1"].weekly
    requested = WeeklySchedule((
        WeeklyTimeRanges(
            (Weekday.MONDAY, Weekday.TUESDAY, Weekday.WEDNESDAY,
             Weekday.THURSDAY, Weekday.FRIDAY),
            (TimeRange("06:00", "09:00"), TimeRange("16:00", "22:00")),
        ),
        WeeklyTimeRanges(
            (Weekday.SATURDAY, Weekday.SUNDAY), (TimeRange("08:00", "22:00"),),
        ),
    ))
    async with AsyncHargassnerClient(host, write_policy=WritePolicy.only(program.name)) as client:
        current = await client.async_get_parameter(program.name)
        preview = await client.async_preview_parameter_write(program.name, requested)
        # Inspect/confirm; this refuses if D9 is not already weekly.
        return await client.async_set_parameter(program.name, requested, expected=current.value)
```

For daily operation use `DailySchedule((TimeRange("06:00", "09:00"), ...))`
and the `.daily.name`. The values above illustrate syntax, not recommended
heating times. Existing read values (tuples of ranges/groups) and wire-shaped
JSON arrays are also accepted, so `expected=current.value` works without
manually reconstructing the controller's unused slots.

The initial writer deliberately supports the reviewed layout and semantics:

- **Two windows per daily program/day group**, and **one or two weekly day
  groups**. The firmware limits weekly groups; the SDK also keeps the normal
  two-window layout. It does not promise seven independent daily patterns.
- Weekday IDs are **1 Monday … 7 Sunday**, matching Qt's `QDate::dayOfWeek`.
  Python `date.weekday()` uses a different 0-based convention; use `Weekday` or
  `date.isoweekday()`, never guess the mapping. Bool/float/other-enum days fail.
- Times use the **controller's local wall clock**, at minute resolution.
  `HH:MM` and `HH:MM:00` normalize to `HH:MM:00`. Start is inclusive, end
  exclusive; `24:00` is allowed only as the end of the day. No UTC conversion,
  DST correction, date-specific exception, or clock-source change is performed.
- Windows must have start < end and not overlap. Overnight ranges such as
  22:00→06:00 are rejected: split them explicitly at midnight/on the next day,
  within the group/window limits. The SDK does not silently move weekdays.
- A weekly **full replacement must explicitly cover all seven days once**, with
  disjoint groups. To have no comfort windows on some days, give those days a
  group with `time_ranges=[]`. This means reduced operation in Automatic,
  **not** an unconditional circuit-off command.
- Empty daily schedules clear comfort windows. Daily serialization pads to the
  reviewed two slots with 00:00→00:00. Weekly serialization instead removes
  those unused zero-length slots: the weekly JSON parser rejects them as real
  intervals. Empty weekday groups from reads are ignored in comparisons.
- This replaces the **entire selected zone program**, not a partial interval
  patch or a merge. Requested/expected inputs are copied to normalized immutable
  values before network awaits. `expected=` is mandatory for actual writes.

Every preview/set reads the chosen program, daily/weekly peer, configured
circuit type and D9 in one selected GET. The program must be active USER with
the reviewed type, its peer inactive, and the circuit configured/present. A
daily controller program must have the reviewed two slots. Ambiguous/missing
data and unselected program variants are refused. Selectors stay internal and
are never submitted in the POST. Successful updates send one sparse parameter
entry; verification repeats the context check and compares schedule behavior,
allowing equivalent group ordering, padding, or adjacent-window representation.
Clock/profile changes after POST or uncertain responses produce unverified
outcomes, never replay, rollback, forced mode changes, or a stronger login.

`hvctl set` accepts a JSON array as its value and `--expect` accepts the current
value array. For example, a **preview-only** daily request is:

```bash
hvctl <boiler-ip> set 3 '[{"start":"06:00","end":"09:00"},{"start":"16:00","end":"22:00"}]'
```

Applying additionally requires `--apply --expect '<current-value-array>'`;
do not submit an entire GET metadata object. Mode/numeric CLI inputs keep their
existing scalar format. `CIRCUIT_SCHEDULE_WRITES` is the six-name writer subset;
boiler/DHW/FWS schedules, D9, D5, and other structured values remain unwritable.

### Unverified outcomes and errors

`HargassnerError` is the common base. Specific errors distinguish transport,
HTTP status/reason, authentication/cooldown, malformed JSON/protocol, missing or
unsupported parameters, validation, and write policy refusals.

- `WriteMismatchError`: POST/GET values do not match the request.
- `WriteOutcomeUnknownError`: a write response or read-back could not be verified.

Both derive from `WriteVerificationError` and carry `.result` with observed
supported-parameter data and optional `.write_error`/`.read_error`. Even a matching read-back
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

The contract was reviewed against 2.7.4 firmware and 3.4.0 responses. Live
reads and previews have passed on firmware reporting `smart_hv_3.4.0`.
The library's actual writes, physical heating effects, and reboot persistence
remain unverified on hardware. Synthetic tests cover serializers, validation,
read-back, silent rejection, authentication recovery, and uncertain outcomes;
passing them is not a guarantee of compatibility with every installation.

Not provided:

- General installer/developer access, arbitrary endpoint calls, boiler-wide
  mode changes, ignition, direct actuator commands, or identity mutations.
- Writes to other schedules, ordinary booleans, dates, text, circuit-type
  selectors, global D9 selection, or flow-temperature limits.
- Automatic channel-unit/state-code decoding, authoritative hardware
  discovery, batch transactions, write replay, or rollback.
- A Home Assistant integration, recurring polling, historical storage, or a
  background service. Those remain the responsibility of a separate consumer.

`daqOnly=true` avoids requesting SERVICE, but is not a server-side read-only
permission. The library cannot detect or undo SERVICE entered by another
client, or restore a whole-boiler operating mode.

### Offline tests

From a source checkout or unpacked source distribution:

```bash
.venv/bin/python -m pip install -e '.[test]'
HVCLIENT_LIVE=0 .venv/bin/python -B -m unittest discover -s tests -v
```

The offline suite uses synthetic data and loopback HTTP servers. Hardware
tests are skipped by default; `HVCLIENT_LIVE=0` explicitly keeps them disabled.

### Optional hardware smoke test

The source distribution includes an optional read/preview smoke test:

```bash
HVCLIENT_LIVE=1 HVCLIENT_LIVE_HOST=boiler.example \
  .venv/bin/python -B -m unittest tests.test_library_live -v
```

Replace `boiler.example` with an explicit bare hostname/IP. The test runs
serially on HTTP port 80, reads the supported endpoints, previews representative
active controls, checks inactive-program refusal and public limits, and rereads
the previewed settings. Extra guards prohibit setters and parameter POSTs;
only `daqOnly=true` authentication may POST. Each request has a 10-second
timeout and authentication attempts are bounded. No captures are saved.

Both enable flag and host are required. CI/pytest-xdist workers are refused.
Concurrent local UI/client changes or one-time mode transitions can legitimately
fail the final comparison; the test does not retry or restore settings.

## Licence

**Apache-2.0.** The standard `LICENSE` and detailed `LICENSING.md` scope/provenance
notice are included in source and wheel distributions. In installed wheels,
they are in the distribution's `.dist-info/licenses/` directory.
