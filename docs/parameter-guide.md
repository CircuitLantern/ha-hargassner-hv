<!-- SPDX-License-Identifier: MPL-2.0 -->

# Smart HV parameters — English interpretation and manual crosswalk

This guide explains what the HTTP parameters control, rather than just how to
send requests. It combines the German service/electrical manuals with the local
API samples. It is **not** a commissioning procedure, a set of recommended
values, or proof that a particular setting is safe to change over HTTP.

## 1. Sources, versions, and confidence

| Source | Edition / scope | Use here |
|---|---|---|
| **SHB** — [Service manual](../manuals/shb-smart-hv-17-23-de.pdf) | Smart-HV 17–23; cover: **DE V03, 03/2021, 11060355**; 16 PDF pages | Customer parameters p. 3; installer parameters pp. 4–5; service parameters pp. 6–8; combination-boiler parameters p. 9 |
| **EHB** — [Electrical manual](../manuals/ehb-smart-hv-17-23-de.pdf) | Smart-HV 17–23; cover: **DE V08, 12/2024, 11060297**; 12 PDF pages | Board/sensor names, physical heating-circuit and cylinder connections, CAN/Ethernet layout |
| **API** — [Parameter sample](../captures/parameters.sample.jsonc) and [identity sample](../captures/info.sample.jsonc) | Unit reports **`smart_hv_3.4.0`**; parameter descriptions are mostly French, with some German | Exact wire `name`, current display label, type, choices, ranges, and activation metadata |
| **CODE** — `smart_hv_2.7.4/data/app/bin/app.kuna/` | Analyzed 2.7.4 executable | Previously traced routing, parameter setters, classifications, and channel/parameter distinction; see [HTTP reference](boiler-http-api.md#52-parameters) |

Manual PDFs and API captures are local reference inputs, not tracked in Git.
Their relative links resolve only when those files are supplied at the paths
listed above.

Page references below are the printed page numbers, which agree with the PDF
page numbers for the cited tables. The SHB PDF metadata says it was produced
in 2022; **03/2021 is the edition printed on the cover**, not a firmware version.
The EHB is a later hardware document. Neither manual specifies a 3.4.0 HTTP API.

Source notation:

- **SHB n / EHB n:** English paraphrase of that manual page, cross-referenced to
  API identifiers where the meaning agrees.
- **API:** interpreted from the captured object's description/help/metadata;
  not documented by these manuals at that identifier.
- **VERIFY:** a discrepancy, uncertain mapping, or behavior still needing code
  inspection or controlled device validation.

The parameter sample contains **442 entries**: 44 USER, 130 PLUMBER, 148 SERVICE,
and 120 DEVELOPER. This guide covers every USER entry, the SHB parameter families,
and the important installer/service relationships. Later solar, fresh-water,
and developer extensions are discussed separately, without pretending that the
SHB documents all 442 entries or their control algorithms.

## Contents

- [Plant terminology and hardware context](#2-understand-the-plant-before-interpreting-a-value)
- [Manual-to-API identifiers and schedule semantics](#3-matching-a-manual-row-to-the-api)
- [All customer-level parameters](#4-all-customer-level-parameters-in-english)
- [Installer parameters and dependencies](#5-installer-parameters-why-the-customer-settings-behave-as-they-do)
- [Service parameter crosswalk](#6-service-parameters-english-crosswalk)
- [API-only extensions](#7-api-extensions-not-documented-by-these-manual-tables)
- [Discrepancies and unresolved mappings](#8-discrepancies-and-things-not-to-infer)
- [Library and Home Assistant implications](#9-consequences-for-a-reusable-library--home-assistant-integration)

## 2. Understand the plant before interpreting a value

### 2.1 German terminology

| German term / abbreviation | English meaning in these manuals | Important distinction |
|---|---|---|
| Stückholz / Kessel / TK | Wood-log fuel / the wood-fired boiler / boiler-water temperature | Not the domestic-hot-water cylinder |
| Boiler / TBoiler | Domestic-hot-water (DHW) cylinder / cylinder temperature | A parameter called “Boiler 1” normally concerns tap water, not combustion |
| Puffer | Heating-water buffer/thermal store | A hot-water energy store feeding heating/DHW loads; not necessarily potable water |
| Heizkreis / HK / Hk | Space-heating circuit or zone | Its pump/mixing valve supplies radiators or underfloor heating |
| Vorlauf / VL | Heating-water flow/supply to a circuit | Flow-temperature limits are not room-temperature setpoints |
| Rücklauf / RL | Heating-water return | Boiler return-temperature protection is distinct from room comfort |
| Rücklaufanhebung | Boiler return-temperature raising/protection system | Pump/mixer control that keeps the returning water sufficiently warm |
| Mischer | Mixing valve | Travel time, minimum actuation pulse, and initial opening are different settings |
| Steilheit | Heating-curve slope | Relationship between outdoor temperature and required flow temperature |
| Soll / Ist | Target/setpoint / measured actual value | Setting a target does not directly set a sensor reading |
| Absenkung | Reduced-temperature/setback operation | Not necessarily “off”; frost protection and other overrides can remain relevant |
| Freigabe / Anforderung | Permission/enabling condition / demand/request | A permitted time window is not a guarantee of heating or pump operation |
| Schaltdifferenz / Hysterese | Switching differential / hysteresis | Usually a temperature difference, not another absolute temperature target |
| Spreizung / Überhöhung | Temperature spread / additional temperature margin | Do not translate either automatically as hysteresis |
| Restwärme | Residual-heat recovery | Moving useful remaining heat after combustion, subject to control conditions |
| Saugzug / SZ | Induced-draught/exhaust fan | A combustion actuator, not ventilation for a room |
| Rauchgas / RGT | Flue gas / flue-gas temperature | Different sensor and thresholds from boiler-water temperature |
| PLK / SLK | Primary-air flap / secondary-air flap | Combustion-air actuators |
| STB | Safety temperature limiter/thermostat | Safety hardware, not an ordinary operating setpoint |
| Anheizen / Leistungsbrand / Ausbrand | Lighting/start-up / established firing / burnout | Phases in the boiler sequence; not Home Assistant HVAC modes |
| FR35 | Room control unit | Sensor influence, local adjustment, and its displayed information are separate concepts |
| HKM / HKA | Heating-circuit module / heating-circuit-A board | Configuration labels refer to installed expansion hardware |
| FWS | Fresh-water/DHW heat-exchanger station | Alternative DHW arrangement to a stored potable-water cylinder; API extension here |

A useful conceptual model is:

```text
wood-fired boiler → protected boiler/buffer water circuit → thermal store
                                                      ├→ mixed heating circuit(s)
                                                      ├→ DHW cylinder charging
                                                      └→ optional fresh-water station
```

This is context, **not an installation-specific hydraulic diagram**. The
configured cylinder mode, buffer arrangement, and optional modules change the
actual control paths (SHB pp. 4–5).

### 2.2 What the electrical manual establishes

- EHB pp. 4–5 identifies buffer sensors **top/middle/bottom** (`Ac high`,
  `Ac mid`, `Ac low`), outdoor sensor (`Out-AT`), boiler-water sensor (`Boil-TK`),
  return sensor (`Ret-RL`), flue-gas sensor (`w Flue g`), Lambda probe, and STB.
  Thus similarly named API temperatures refer to different physical quantities.
- EHB pp. 5 and 11 distinguishes the boiler/buffer return pump and return mixer
  from combustion fan and air-flap motors. “Pump”, “mixer”, and “temperature”
  settings must always be interpreted with their subsystem.
- EHB p. 7 shows **Boiler A** and **Heizkreis A** sensor/pump connections on the
  I/O 36.1/HKA board. The `A` suffix is a controller/hardware designation, not
  an arbitrary room name or “automatic” mode.
- EHB p. 8 labels one HKM-0 arrangement as heating circuits 3/4 and cylinder 2,
  whereas the SHB and sample use 1/2/A and cylinder 1/A. **VERIFY** numbering
  against the installed modules and controller UI; do not infer a CAN address
  or physical room solely from an API suffix.
- EHB pp. 9–10 shows Ethernet to the router and CAN links to expansion boards,
  including different HKA board locations. LAN API access is not a direct
  connection to each room unit.

The EHB warns that electrical work is for qualified personnel (p. 3). No wiring,
safety-chain bridging, or hardware-address changes are implied by this guide.

## 3. Matching a manual row to the API

The manual's **Menü** column normally corresponds to an API display label,
**not** the JSON `name` used for requests. Match label **and meaning/type**.

| Manual/menu meaning | Captured `displayName` | Actual API `name` |
|---|---|---|
| Hk1-2, daytime room target | `Hk1-2` | `4` |
| B1-2, DHW cylinder 1 target | `B1-2` | `2` |
| E5a, controller clock | `E5a` | `Setup6` |
| A7, circuit 1 pump enable temperature | `A7` | `L13` |
| B31, cylinder A configuration | `B31` | `B11` |
| K2, fan at full output | `K2` | `K3` |
| K38, chimney-sweep operating duration | `K38` | `K4a` |

For example, sending the manual label `K38` as the API name would select the
captured **oxygen-ramp start value** (display `K32`), not the chimney-sweep
duration. Similar-looking numeric codes are not interchangeable.

Other rules:

- Repeated labels are intentional in places: B1-1/Hk1-1 have daily and weekly
  schedule objects; A2/A3/A4 have radiator and underfloor variants. Store
  separate records by `name`, not by label.
- `active` means the parameter is applicable/active in the current configuration.
  It does not mean that heating is currently on, nor is it an HTTP permission.
- USER/PLUMBER/SERVICE/DEVELOPER describe catalog/UI levels. A “service
  parameter” is not the same concept as `SystemMode::SERVICE`.
- A broad numeric range is a software acceptance range, not a recommended or
  safe commissioning range. Get current metadata for the deployed firmware.
- Choices use IDs; translations are display text. A boolean may represent
  “automatic/manual” rather than “off/on”. Read `trueText` and `falseText`.
- A channel is an observed value, not automatically a parameter with the same
  identifier. `/channels` does not provide writes; some underlying settings have
  separate parameter identifiers.

### 3.1 Schedule and clock interpretation

SHB p. 5, **E14** (API `D9`) selects daily or weekly schedules. In the capture,
false means daily and true means weekly. Which objects are active matters;
do not write both versions merely because their display labels are identical.

- Daily ranges are arrays of `{"start":"HH:MM:SS","end":"HH:MM:SS"}`.
- Weekly values group `weekdays` with `time_ranges`. Subsequent 2.7.4 code/
  assembly inspection confirms Qt/ISO IDs **1=Monday through 7=Sunday**:
  `WeeklyClockPart::checkWeekdays` compares them to `QDate::dayOfWeek`.
- `00:00:00` → `24:00:00`, zero-length ranges, and empty weekday arrays occur.
  Preserve them when reading and do not coerce `24:00:00` to an ordinary Python
  same-day time. In the reviewed write path daily programs need their existing
  slot count (normally two), while weekly parsing rejects zero-length time
  ranges. The library pads daily unused slots but strips weekly unused slots,
  uses minute-resolution intervals, and refuses overnight/overlapping ranges.
- **E12** (API `D5`) determines the **day-versus-night setback classification**
  used by Hk-12/Hk-13. It is not the same as a circuit's comfort-heating schedule.
- E5/E5a/E6 (`Setup5`/`Setup6`/`Setup7`) are time source, clock, and time zone.
  The sample's `Setup6` date contains invalid/unset components; do not substitute
  the current date or write it back. Exact datetime encoding/time-zone behavior
  needs serializer validation; the manuals explain meaning, not JSON formats.

The library now supports full replacements for the six USER circuit programs
(`3`/`3a`, `6`/`6a`, `HP3`/`HP3a`), with explicit policy and expected values,
one/two weekly day groups, two windows per group, and complete explicit weekday
coverage. It checks active chosen/inactive peer programs, circuit presence,
and D9 before and after the write; it never changes D9 or operating mode. Other
DHW/FWS schedules remain read-only. See [the standalone program contract](library.md#automatic-comfort-programs-daily-and-weekly-schedules).
Code/capture agreement is not a recorded successful live schedule-write test.

## 4. All customer-level parameters, in English

All **44 USER entries** in the sample are represented below, including inactive
ones. SHB p. 3 documents 30 of these daily/weekly/customer entries. The other
14 are identified from API metadata, not silently attributed to the old manual.
Types, limits, and captured activation flags are tabulated separately in the
[HTTP reference](boiler-http-api.md#all-user-parameters-code-catalog-and-live-sample).

### 4.1 Room comfort and heating schedules

| Menu | API `name` | English meaning | Source |
|---|---|---|---|
| Hk1-1 | `3` | Circuit 1 daily comfort-heating windows, same daily program | SHB 3 |
| Hk1-1 | `3a` | Circuit 1 weekly comfort-heating windows | SHB 3 |
| Hk1-2 | `4` | Circuit 1 daytime/comfort room-temperature target | SHB 3 |
| Hk1-3 | `5` | Circuit 1 reduced/setback room-temperature target outside heating windows | SHB 3 |
| Hk2-1 | `6` | Circuit 2 daily comfort-heating windows | SHB 3 |
| Hk2-1 | `6a` | Circuit 2 weekly comfort-heating windows | SHB 3 |
| Hk2-2 | `7` | Circuit 2 daytime/comfort room-temperature target | SHB 3 |
| Hk2-3 | `8` | Circuit 2 reduced/setback room-temperature target | SHB 3 |
| HkA-1 | `HP3` | Circuit A daily comfort-heating windows | SHB 3 |
| HkA-1 | `HP3a` | Circuit A weekly comfort-heating windows | SHB 3 |
| HkA-2 | `HP4` | Circuit A daytime/comfort room-temperature target | SHB 3 |
| HkA-3 | `HP5` | Circuit A reduced/setback room-temperature target | SHB 3 |
| Hk-11 | `11` | Outdoor-temperature shutdown threshold for all heating circuits, independent of heating windows | SHB 3 |
| Hk-12 | `12` | Outdoor-temperature shutdown threshold during **day setback** | SHB 3 |
| Hk-13 | `13` | Outdoor-temperature shutdown threshold during **night setback** | SHB 3 |

The room targets are not measured room temperatures and not water-flow targets.
Their effect depends on weather compensation, heating-curve settings, room-sensor
configuration, and operating state (SHB pp. 3–4, 8). Do not assume a generic
on/off thermostat interface.

Hk-11/12/13 compare against **averaged outdoor temperature**, not necessarily
the instantaneous outdoor reading. Crossing a threshold can explain why a zone
does not heat despite a comfort schedule. Setback is not simply “night”: E12
defines which setback threshold applies. Safety overrides need separate handling;
these parameters do not redefine frost-protection or STB behavior.

### 4.2 Domestic hot water and its circulation

| Menu | API `name` | English meaning | Source |
|---|---|---|---|
| B1-1 | `1` | DHW cylinder 1 daily charging windows | SHB 3 |
| B1-1 | `1a` | DHW cylinder 1 weekly charging windows | SHB 3 |
| B1-2 | `2` | DHW cylinder 1 target water temperature | SHB 3 |
| BA-1 | `HP1` | DHW cylinder A daily charging windows | SHB 3 |
| BA-1 | `HP1a` | DHW cylinder A weekly charging windows | SHB 3 |
| BA-2 | `HP2` | DHW cylinder A target water temperature | SHB 3 |
| B1-3 | `boiler1.zirk.freigabe` | Cylinder 1 DHW recirculation permission windows; not cylinder charging windows | API |
| BA-3 | `boilerA.zirk.freigabe` | Cylinder A DHW recirculation permission windows | API |

The cylinder target alone does not determine when charging runs. SHB pp. 4–5
adds a scheduled reheat differential, an immediate minimum-temperature trigger,
legionella cycles, and optional buffer differential charging. Recirculation
means keeping the hot-water distribution loop warm; it is not the space-heating
pump or the cylinder loading pump. Hygiene and scalding constraints are not
established by the API's 0–75 °C range.

### 4.3 Display, language, and time

| Menu | API `name` | English meaning | Source |
|---|---|---|---|
| E1 | `Setup1` | Enable automatic display standby; sample true = Auto, false = Disabled | SHB 3; API boolean labels |
| E1a | `Setup1a` | Inactivity time before display standby | SHB 3 |
| E2 | `Setup2` | Time before returning to the controller's home screen | SHB 3 |
| E3 | `Setup3` | Display backlight brightness | SHB 3 |
| E4 | `Setup4` | Controller operating language | SHB 3 |
| E4a | `Setup4a` | Internal language-file path; not a normal comfort control | API |
| E5 | `Setup5` | Time-source selection; sample false = Internet, true = Manual | SHB 3; API boolean labels |
| E5a | `Setup6` | Manually entered controller date/time; time-source settings can override it | SHB 3 |
| E6 | `Setup7` | Controller time-zone selection | SHB 3 |
| E7 | `Setup8` | Select the two values shown on the controller home screen | SHB 3 |

E7 concerns the physical controller display, not which Home Assistant sensors
exist. Time changes can shift heating/loading schedules. The manuals do not
define an HTTP/UTC time conversion or validate arbitrary language-file paths.

### 4.4 Optional systems and newer customer controls

These are **API-derived meanings**; the supplied SHB does not list these menu
rows in its customer table. Hardware references provide context, not setter proof.

| Menu | API `name` | English meaning / qualification |
|---|---|---|
| FWS1-1 | `fws.freigabe.tagesuhr` | Daily permission windows for keeping the buffer ready for the fresh-water station |
| FWS1-1 | `fws.freigabe.wochenuhr` | Weekly version of the same readiness program |
| FWS1-2 | `fws.auslaufSoll` | Desired DHW outlet temperature from the fresh-water station |
| FWS1-5 | `fws.freigabe.tagesuhr.zirk` | Daily DHW recirculation windows for the fresh-water station |
| FWS1-5 | `fws.freigabe.wochenuhr.zirk` | Weekly version of the fresh-water-station recirculation program |
| Fw1 | `kessel.fremdwaerme.freigabezeit` | When requests to another heat source are permitted; associated external-heat output is shown in EHB 4–5 |
| EKombi0 | `kombi.freigabezeit` | When a request to the paired/slave boiler is permitted; SHB 9 explains the combination-boiler roles, not this schedule |
| 21b | `mode.remote_control.enabled` | Allow the application to change the boiler operating mode, according to API help; **not proven to gate all HTTP parameter writes** |
| 21c | `mode.remote_control.lock_duration` | Duration of app remote-control blocking after interaction at the local controller, according to API help; scope **VERIFY** |
| E19 | `E18` | Immediate-ignition request; command-like/high-impact boolean, not an innocuous preference; exact latching/conditions **VERIFY** |
| Z91 | `Z91` | Earliest start time according to the label; help says only “Test”; purpose, validity, and command interaction **VERIFY** |

Do not confuse API name `E18` (display **E19**, immediate ignition) with display
**E18** (API name **`D1`**, automatic-ignition configuration). Neither is described
as a customer ignition control by SHB p. 3. Presence in USER is not approval for
an automatically exposed Home Assistant switch or button.

## 5. Installer parameters: why the customer settings behave as they do

### 5.1 Heating-circuit configuration — SHB p. 4

Each slash-separated list below is ordered **circuit 1 / circuit 2 / circuit A**.
These are installer configuration, not room comfort commands.

| Manual menus | API names | English interpretation |
|---|---|---|
| A1 / A11 / A61 | `A1` / `A11` / `A61` | Circuit type: absent, radiator, or underfloor configuration in the sample |
| A1a / A11a / A61a | `A1a` / `A11a` / `A61a` | Circuit hydraulic/mixer control: whether a mixing valve is used |
| A2 / A12 / A62 | `A2`, `A2a` / `A12`, `A12a` / `A62`, `A62a` | Heating-curve slope; separate profile objects share each menu label |
| A3 / A13 / A63 | `A3`, `A3a` / `A13`, `A13a` / `A63`, `A63a` | Minimum heating-water flow temperature, profile-dependent |
| A4 / A14 / A64 | `A4`, `A4a` / `A14`, `A14a` / `A64`, `A64a` | Maximum heating-water flow temperature, profile-dependent |
| A5 / A15 / A65 | `A5` / `A15` / `A65` | Full mixing-valve travel time (closed to open), not the heating duration |
| A6 / A16 / A66 | `A6_new` / `A16_new` / `A66_new` | Room-unit selection; the newer API offers more than the manual's FR35 enable/disable description |
| A7 / A17 / A67 | `L13` / `L14` / `L19` | Boiler/buffer temperature above which the circuit pump is permitted to run |

Profile selection needs care. In circuit 1 the sample labels `A2` as the radiator
slope and `A2a` as underfloor; **circuit 2's help labels are reversed** (`A12` says
underfloor, `A12a` says radiators). Its active slope is `A12a`, while its active
minimum/maximum objects are the `a` variants. This is inconsistent enough that
a suffix-only rule based on translated help is unsafe. Subsequent inspection
of **2.7.4 code/assembly** resolves that version's selection: in
`HvHeizkreis::internalInitializeParameters` (`0x3aa2c`), circuit type ID 2 selects
`A2a`/`A12a`/`A62a`; the other branch selects the unsuffixed names. The selected
float is used by `HeizkreisBase::CalcHeizkreisSoll` (`0x146388`) as the
dimensionless slope in the normal flow-target calculation. Thus the reversed
circuit-2 help text is not the authoritative selector. The 3.4.0 sample's choice
IDs and active flags agree with that mapping, but deployed control effects
remain **VERIFY**; no live curve write is recorded.

The library now permits exactly these six slope exceptions, not general PLUMBER
access. It checks the live selector ID, active chosen variant/inactive peer,
FLOAT type, empty unit, current bounds/increment, and required expected value.
It never changes circuit type or min/max flow limits, and it keeps
`daqOnly=true`. See [the library's curve policy](library.md#heating-curve-slopes-narrow-installer-exceptions).
Being writable through that policy is not proof that a slope suits an installation.

Other newer room-unit fields are **API-only** relative to the supplied manual:

- `hk1.fr35.sensor_new`, `hk2.fr35.sensor_new`, `hkA.fr35.sensor_new`: room-sensor
  presence/use; separate from selecting the room-unit type.
- `hk1.fr35.anzeige_new`, `hk2.fr35.anzeige_new`, `hkA.fr35.anzeige_new`: which
  cylinder/buffer/FWS temperature the room unit displays, not its room setpoint.
- `hk1.ausBeiRaumtemp.aktiv_new`, `hk2.ausBeiRaumtemp.aktiv_new`,
  `hkA.ausBeiRaumtemp.aktiv_new`: room-temperature-based circuit shutdown option.
- Legacy `A6`/`A16`/`A66` and legacy `hk*.ausBeiRaumtemp.aktiv` objects coexist
  with newer fields. They are not interchangeable aliases.
- `Hk1-2a`, `Hk2-2a`, `HkA-2a`: room-target offsets in the API, distinct from
  base daytime targets; runtime/hardware linkage is code-derived, not explained
  by the supplied SHB. `A1z`/`A11z`/`A61z` are internal circuit-mode choices:
  0 Off, 1 Automatic, 2 permanent reduced, 3 permanent comfort, 4 one-time
  comfort, and 5 one-time reduced/short absence. They are DEVELOPER entries
  deliberately hidden with `AlwaysInactiveCondition`, not the PLUMBER circuit
  type selectors `A1`/`A11`/`A61`. The captured choices and 2.7.4 code agree;
  mode changes have not been live-tested. The library now permits exactly
  these three mode exceptions with explicit policy/expected IDs and configured
  circuit checks; see [circuit-mode support](library.md#circuit-operating-modes-narrow-hidden-choice-exceptions).
  These IDs are unrelated to the whole-boiler SERVICE mode.

### 5.2 Cylinder charging, priority, and hygiene — SHB pp. 4–5

| Menu for cylinder 1 / A | API name for 1 / A | English interpretation |
|---|---|---|
| B1 / B31 | `B1` / `B11` | Cylinder presence/type; manual associates 1 with HKM and A with HKA hardware |
| B2 / B32 | `B2` / `B12` | Scheduled reheat differential: how far below the cylinder target before recharging inside loading windows |
| B3 / B33 | `B3` / `B13` | Minimum cylinder temperature that requests immediate recharge, rather than only scheduled reheat |
| B4 / B34 | `B4` / `B14` | Enable the legionella-protection program |
| B5 / B35 | `B5` / `B15` | Higher cylinder target used by that protection program |
| B6 / B36 | `B6` / `B16` | Weekly schedule for that protection program |

| Shared menu | API name | English interpretation |
|---|---|---|
| B50 | `O9` | Buffer differential charging: keep the cylinder at target using available buffer heat; otherwise normal differential/minimum conditions govern reheat |
| B51 | `N5` | DHW-priority reduction of heating-circuit flow targets: Off, Auto, or Max; manual describes Max as reduction to 10 °C |

“Permission windows” are not the complete decision rule: minimum-temperature,
hygiene, heat availability, and priority functions can interact. The manuals do
not establish what every external/internal cylinder choice means for every
installation. Do not remove hygiene protection or infer safe DHW temperatures
from software limits.

Newer recirculation fields (`boiler1.zirk.*`, `boilerA.zirk.*`) separately describe
pump presence, on time, and pause time. Their help is API evidence, not an SHB
description of a continuous loading-pump command.

### 5.3 Buffer, return protection, external demand, and frost — SHB p. 5

| Menu | API name | English interpretation |
|---|---|---|
| C1 | `C1` | Whether a heating-water buffer is configured |
| C2 | `C2b` | Configured buffer volume |
| C6 | `C3a` | Buffer charge-level threshold for a “refuel/add wood” indication; not a combustion-power command |
| C7 | `ruecklaufbase.rwbisPufferfuehler` | Buffer reference sensor used as an end condition for residual-heat recovery |
| C7a | `C1a` | Boiler return-temperature protection arrangement/type |
| C7b | `C1b` | Return-mixer travel time; manual says to use the actuator's rating |
| C8 | `C6` | Flow-temperature request for an external heating demand at the EXT input |
| E10 | `D2` | Outdoor-temperature threshold for heating-circuit frost-protection pump operation |
| E10a | `D4` | Boiler-water temperature below which boiler frost protection starts the return pump |
| E11 | `D3` | Heating-circuit flow target during frost protection |
| E12 | `D5` | Time ranges classifying day versus night setback for Hk-12/Hk-13 |
| E13 | `D13` | Outdoor-sensor presence; manual specifies a fictitious −10 °C value if absent |
| E14 | `D9` | Daily versus weekly scheduling selection |
| E15 | `D14` | Display the lighting/start-up assistant on the controller |

SHB describes EXT as a potential-free input, open = off, closed = heat. This
explains `C6`'s meaning; it is **not an instruction to bridge terminals**. EHB
pp. 4–5 shows EXT separately from the external-heat-source demand output.

`puffer.fuehlerauswahl` (display C3), buffer forced-charge settings (`C5`,
`puffer.ausBeiAussentemp`), and `kessel.fremdwaerme.aktiv` extend this configuration
in the captured API. Buffer sensor selection, force-charge schedules, and an
output to another heat source are different controls.

## 6. Service parameters: English crosswalk

These parameters describe combustion, protection, and hydraulic tuning. Their
presence over HTTP must **not** turn them into normal Home Assistant controls.
The tables are semantic references, not instructions to adjust safety limits or
burner tuning. Check each object's actual `securityLevel`; the manual's section
title does not always match the modern API classification.

### 6.1 System configuration — SHB p. 6

| Menu | API `name` | English interpretation |
|---|---|---|
| E0 | `Z0` | Boiler output/power class |
| E01 | `system.parameter_reset` | Reset selected parameter groups; manual excludes commission number and boiler ID |
| E01a | `system.factory_reset` | Whole-system parameter reset with the manual's stated identity exceptions; destructive |
| E02 | `ZX` | Manufacturer identity/branding selection |
| E03 | `ZY` | Exhibition/demo configuration |
| E07 | `Z8` | Factory-assigned commission/installation number |
| E08 | `vnc.enabled` | VNC support; manual warns of restart and a black local display |

These are settings/actions, not ordinary telemetry. Do not assume that resetting
this parameter has identical effects to the method-agnostic `/factory-reset`
HTTP endpoint without tracing the relevant implementation.

### 6.2 Fan, air-flap, and fault-detection settings — SHB p. 6

| Manual menu(s) | API `name`(s) | English interpretation |
|---|---|---|
| K1a / K1b | `K1a` / `K2a` | Minimum / maximum regulated boiler output |
| K2 | `K3` | Exhaust-fan command at 100% output, expressed relative to maximum speed |
| K3 | `K5c` | Fixed fan command during start-up with the door closed until output control takes over |
| K3a | `K5ca` | Fan command during ignition monitoring after door opening |
| K3b / K3c / K3d | `K5` / `K5a` / `K5b` | Door-open fan ramp points at flue-gas temperatures 20 / 100 / 170 °C |
| K3b / K3c / K3d, Kombi variants | `K5e` / `K5f` / `K5g` | Equivalent ramp points for combination-boiler configuration |
| K4 | `K5d` | Fan command when the safety-temperature limiter/protection condition is active |
| K5 / K6 | `K6` / `K6a` | Primary-air minimum / maximum during output control |
| K6a | `K6d` | Check duration for primary-flap “open” position fault detection |
| K6b | `K6e` | Minimum fan condition used to detect a primary-flap “open” fault |
| K6c | `K6f` | Maximum fan condition used to detect a primary-flap “closed” fault |
| K6d | `K6g` | Minimum flue-gas condition in the primary-flap “closed” check |
| K6e | `K6h` | Maximum flue-gas condition in the primary-flap “open” check |
| K6ad | `K6i` | Check duration for primary-flap “closed” position fault detection |
| K8a / K8b | `K6c` / `K7b` | Fixed primary-/secondary-air flap positions with door open |
| K9a / K9b | `K7` / `K7a` | Secondary-air minimum / maximum during oxygen control in established firing |
| K9c / K9d | `K7e` / `K7f` | Secondary-flap closed-position check duration / detection threshold |
| K10 | `K7c` | Secondary-air fixed position for door-open established firing |

The 20/100/170 °C values identify the **manual's ramp reference points**, not
recommended boiler setpoints. The hydraulic/combustion state and sensor inputs
determine when these commands are used.

### 6.3 Combustion transitions, burnout, and residual heat — SHB p. 7

| Manual menu(s) | API `name`(s) | English interpretation |
|---|---|---|
| K11 | `K7d` | Secondary-air fallback position if the Lambda probe is defective |
| K12 | `K8` | Primary-air maximum during lighting/start-up |
| K13 | `K9` | Maximum start-up time before returning to off |
| K14 | `K10a` | Flue-gas threshold involved in leaving established firing for burnout |
| K15 | `K11` | Flue-gas threshold for start-up → established firing |
| K16 | `K11a` | Residual-oxygen threshold for start-up → established firing |
| K17 | `K11b` | Delay before the oxygen reading is considered valid for that transition |
| K19 | `K13b` | Boiler-temperature indication associated with thermal discharge safety |
| K20 | `K10b` | Burnout-attempt count before reporting failure to reach the required oxygen condition |
| K21 | `K10c` | Flue-gas threshold allowing burnout → established firing, e.g. after refuelling |
| K22 | `K13c` | Boiler-water target during burnout |
| K23 / K24 | `K16` / `K17` | Oxygen threshold and required duration for starting burnout |
| K25 | `K18` | Primary-air fixed position during burnout |
| K26a / K26b | `K43` / `K44` | Minimum / maximum burnout duration |
| K27 | `K45` | Average oxygen value used to regard burnout as complete |
| K28 | `K46` | Minimum burnout extension after opening the door |
| K29 | `kessel.restwaerme.hystEin` | Manual describes boiler/buffer residual-heat transfer hysteresis; captured help describes the pump's switch-on differential |
| K30 / K31 | `K24` / `K27` | Operating-hours / flue-gas thresholds for cleaning indications |
| K32 | `K38` | Initial oxygen-ramp value during start-up |
| K33 / K34 | `K37` / `K39` | Start-up oxygen-ramp timing parameters |
| K35 | `K40` | Oxygen target/ramp value following the initial stage |
| K36 | `K34` | Duration of excessive flue-gas temperature before a cleaning indication |
| K37a / K37b / K37c | `K100` / `K100a` / `K100b` | Fan / primary-air / secondary-air values for an STB overtemperature condition |
| K38 | `K4a` | Maximum chimney-sweep-mode duration before that mode ends |
| K39a / K39b | `K23` / `K32` | Boiler-temperature end condition / allowed duration for residual-heat use |

This table translates the documented roles, **not the full state-machine
priority order**. SHB's K14 title mentions residual heat while its description
specifies entry into burnout; do not merge those states. K33/K34 descriptions
are abbreviated and partially overlapping. The exact ramp sequence remains a
code-level question. `kessel.restwaerme.hystAus` (display K29a) supplies a later
API switch-off differential absent from this SHB row.

### 6.4 Return-temperature protection — SHB pp. 7–8

| Menu | API `name` | English interpretation |
|---|---|---|
| L1 | `L1` | Boiler temperature enabling the return/buffer pump |
| L2a / L2b | `L6` / `L7` | Minimum expected return-protection temperature / time before reporting failure |
| L3 | `L8` | Minimum return-mixer actuation time; do not confuse with full valve travel time C7b |
| L4, conventional / PWM | `L9a` / `L9b` | Boiler-to-return temperature difference enabling the return pump, with separate variants |
| L5 | `L10` | Lower bound for return-temperature target |
| L6a | `L10a` | Nominal return temperature spread before AutoAdapt correction |
| L6b | `L10b` | Limit on the AutoAdapt correction magnitude |
| L6c | `L45` | Minimum established-firing duration used for an AutoAdapt calculation |
| L6d | `L46` | Reference average output used in that calculation |
| L6e | `L47` | Number of burns over which AutoAdapt is averaged |
| L7 | `L11e` | Initial return-mixer opening/dead-time compensation at start of regulation |
| L8 / L9 | `L21` / `L21a` | Cylinder 1 / A loading-pump enable temperature |

These are equipment-protection and commissioning settings. A room-temperature
adjustment is not achieved by changing the boiler's return minimum.

### 6.5 Heat distribution, DHW priority, oxygen, and fan limits — SHB p. 8

| Menu | API `name` | English interpretation |
|---|---|---|
| M1 | `M1` | Boiler-temperature threshold activating heating-circuit overtemperature handling |
| M3 | `M4` | FR35 room-temperature influence on heating-circuit target |
| M4 | `M8` | Minimum heating-circuit mixer actuation time |
| M6 | **No exact menu match in sample** | SHB calls this residual-heat pump switch-on hysteresis; do not substitute the unrelated M6a thermostat hysteresis |
| M7 | `M21` | Initial heating-circuit mixer opening/dead-time compensation |
| N1 | `N1` | Boiler-temperature threshold activating DHW-cylinder overtemperature handling |
| N2 | `N3` | Strength of DHW-priority reduction of heating-circuit flow targets; see naming caveat below |
| N4 | `N4` | Boiler-versus-cylinder temperature difference ending DHW residual-heat handling |
| O1 | `O1` | Additional buffer temperature margin requested by heating circuits |
| O2 | `O3` | Additional buffer temperature margin requested by DHW cylinders |
| O3 | `O6` | Buffer temperature deficit that may trigger forced charging |
| O4 | `pufferbase.schaltdifferenzPufferladung` | Buffer-top temperature margin used to end buffer charging, relative to boiler target |
| O5 | **Conflicting semantics; see §8** | SHB describes a buffer-charge completion **sensor selection**; the sample's O5 is a numeric switching differential |
| S1a / S1b | `S3` / `S4` | Oxygen targets at maximum / minimum output |
| S2a / S2b / S2c | `P10` / `P10a` / `P11` | Flue-gas rise / oxygen drop / absolute oxygen conditions associated with off → start-up |
| S3 | `S1b` | Oxygen target for chimney-sweep mode |
| T1d | `T3d` | Upper flue-gas limit at which fan command is reduced |
| T2a | `T8` | Lower flue-gas condition at which fan command is increased |
| T12 | `T72` | Maximum permitted exhaust-fan speed |
| T38 | `T38` | Oxygen influence on primary-air control enabled; do not confuse with API `T110` |
| T39 | `T38a` | Enable primary-/secondary-air flap checks |

The SHB titles N2 as a D component, but its description and the captured API
describe a general priority-reduction factor. Do not infer it is the same as a
later developer PID derivative gain. Overtemperature handling can also explain
pumps running outside normal comfort/loading windows; it is not an ordinary
scheduled heating demand.

### 6.6 Combination boiler — SHB p. 9

| Menu | API `name` | English interpretation |
|---|---|---|
| EKombi | `kombi.is_active` | Whether the installation uses combination-boiler operation |
| EKombi1 | `kombi.is_master` | Master/slave role of the boiler |
| EKombi2 | `kombi.gemeinsamer_kamin` | Whether the paired boilers share a chimney |
| EKombi2a | `kombi.sz_wenn_brennt` | Fan command when the paired boiler is firing |

The API also has `kombi.pufferfuehler` (buffer-sensor source for slave operation)
and the USER permission schedule `kombi.freigabezeit`. Their exact coordination
logic is not described by this four-row manual table.

## 7. API extensions not documented by these manual tables

The following are useful English interpretations of **API metadata**, not a
manual-backed explanation of every internal algorithm. Optional systems can
appear in `/parameters` even when absent/inactive on the installation.

### 7.1 Fresh-water station (`fws.*`)

Unlike a DHW cylinder target, these settings concern DHW produced through a
fresh-water heat exchanger and its primary/recirculation pumps.

| API identifiers | English meaning |
|---|---|
| `fws.vorhanden`, `fws.schuettleistung` | Station presence and selected station/output variant |
| `fws.auslaufSoll`, `fws.kaltwasserTemp` | DHW outlet target and assumed/measured cold-water inlet temperature |
| `fws.freigabe.tagesuhr`, `fws.freigabe.wochenuhr`, `fws.pufferMinimum` | Buffer readiness program and minimum buffer-temperature condition |
| `fws.pufferladung.fuehler`, `fws.anforderung.ueberhoehung`, `fws.anforderung.hyst` | Buffer-charge reference sensor, extra heat-demand margin, and demand hysteresis |
| `fws.freigabe.tagesuhr.zirk`, `fws.freigabe.wochenuhr.zirk`, `fws.pumpe.zirk` | Recirculation windows and pump selection/type |
| `fws.pumpe.durchfluss`, `fws.pumpe.durchfluss.pwm`, `fws.pumpe.durchfluss.pwm.percent` | Recirculation-flow calibration and PWM command used for that calibration/control |
| `fws.prim_pumpen.zirk`, `fws.prim_pumpen.zirk_times`, `fws.prim_pumpen.zirk_runtime`, `fws.prim_pumpen.zirk_pause` | Primary-side circulation/preheating selection, windows, on time, and pause time; distinct from potable-water recirculation |
| `fws.pumpe.durchlaufzeit`, `fws.pumpe.zirk.ssz` | Recirculation timing parameters |
| `fws.pumpe.pwm.min`, `fws.pumpe.pwm.max`, `fws.pumpe.pwm.pulseInterval` | Primary pump PWM limits and low-speed cycling interval |
| `fws.zapferkennungAb`, `fws.zapferkennungBis` | Draw-off detection/stop thresholds; exact hysteresis direction and units must follow metadata/code |
| `fws.maxTempAuslauf`, `fws.maxTempSchaltDiff`, `fws.vl.fuehler.pos` | Outlet-overtemperature control, re-enable differential, and flow-sensor placement |
| `fws.regler.*`, `fws.regler_durchfluss.*` | Regulator gains, derivative filtering, integral limits/reset conditions, exchanger adaptation, and start/stop tuning; not user outlet targets |

The two captured objects displayed as **B1012** have different API names
(`fws.pumpe.durchfluss.pwm.percent` and `fws.regler.vorspuelschaltdiff`) and
different functions. This is another reason to use `name` as the key.

### 7.2 Solar (`solar.*`)

These refer to a solar-thermal hydraulic subsystem, not photovoltaic electricity.
The SHB supplied here contains no G-parameter table.

| API identifiers / family | English meaning |
|---|---|
| `solar.schema`, `solar.kreise` | Hydraulic scheme and one-/two-circuit selection |
| `solar.fuehler.kreis1`, `solar.fuehler.kreis2` | Reference/storage sensor for differential control in each circuit |
| `solar.kreis1.ein_bei_spreizung`, `solar.kreis1.aus_bei_spreizung`; circuit-2 counterparts | Collector/reference temperature differences for pump activation/deactivation |
| `solar.kreis1.aus_bei_soll`, `solar.kreis2.aus_bei_soll` | Storage charging targets/end conditions |
| `solar.kreis1.umschaltung.spreizung`, `solar.kreis1.umschaltung.maxTemp` | Conditions for changing from the priority circuit to the second circuit |
| `solar.pumpe1.type`, `solar.pumpe2.type`, `solar.pumpe3.type`; corresponding `.pwm.min`, `.pwm.max`, `.pwm.invers` | Pump type, speed limits, and PWM signal polarity |
| `solar.ventil.parallelbetrieb`, `solar.ventil.invers` | Alternating/parallel operation and switching-valve polarity |
| `solar.kollektor_min`, `solar.kollektor_max`, `solar.ref_max` | Collector low/high-temperature and storage high-temperature limits |
| `solar.kollektor_min_diff`, `solar.kollektor_max_diff`, `solar.ref_max_diff` | Re-enable hysteresis for those limits |
| `solar.pumpe_vorlaufzeit` | Pump pre-flush period |
| `solar.wmz.vorhanden`, `solar.impSensor.vorhanden`, `solar.refLiters`, `solar.impulsePerLiter` | Heat/flow measurement selection and calibration, including the reference flow at 50% pump speed |
| `solar.pwm_regler.*`, `solar.pwm_regler_min`, `solar.pwm_regler_max` | Differential-temperature target plus regulator tuning/output bounds; exact gain behavior is not established by labels |

### 7.3 Internal tuning, operational actions, and networking

| API identifiers / family | English interpretation / limit of knowledge |
|---|---|
| `T1`, `T2`, `T3`, `T4a`, `T4b`; `T9`…`T9d` | Flue-gas-limit/boost regulator components and limits, according to API metadata; not manual T1/T2/T3 meanings inferred from numeric names |
| `T100`…`T104`, `T120`…`T123`, `T76`…`T79` | Oxygen and output-regulation tuning, including a P/I/D/A-labeled loop; exact equations **VERIFY** |
| `L11`, `L11b`, `L11d`, `rl_pwm.*`, `rl.autoadapt.maxSollDiff` | Return-protection regulation and adaptation tuning; not the basic comfort setpoints |
| `M11`, `M11b`, `M11d`, `N10`…`N13c` | Heating-circuit / DHW-priority regulator tuning; distinguish these from the manual's basic M/N settings |
| `lk.motor`, `lk.ref.speed`, `T11`, `T14`, `T14a`, `T40` | Air-flap stepper hardware, reference/homing behavior, backlash compensation, and currents |
| `S11`, `S11a`, `T30`, `rtc.trim`, `waermemenge.offset` | Lambda-heater mode/power, flue-gas measurement scaling, clock trim, and heat-calculation offset; calibration/tuning, not comfort controls |
| `P1`, `P2`, `P3`, `P6`, `zuendueberwachung_maxTime` | Automatic-ignition equipment, timings, secondary-air setting, and monitoring limit |
| `errors.acknowledge_infos`, `errors.acknowledge_errors` | Command-like acknowledgement settings; acknowledgment does not fix a fault |
| `vnc.enabled`, `rsync.enabled`, `grafana.*`, `mqtt.*`, `file.save_interval`, `can.timeout` | Device-side diagnostic/network/file services and timing; not a reason to plan a separate integration data store |
| `weather.*`, `waermemenge.visible`, `ul_ca.is_active`, `asnr` | Weather display/location, heat-quantity display, certification variant, and factory article serial metadata |

Some labels contain encoding damage, placeholders such as “help text”, or just
“Test”. Translate only the recognizable purpose, and keep the actual behavior
unresolved instead of inventing semantics from the parameter prefix.

## 8. Discrepancies and things not to infer

1. **O5 meaning changes/conflicts.** SHB p. 8 describes selecting the sensor to
   which a buffer is charged for a combination-boiler request. API
   `pufferbase.schaltdifferenzBoilertemp`, displayed O5, is a numeric DHW-related
   switching differential. The API also contains a sensor selector
   `pufferbase.pufferladungBisFuehler` under display C3a, which is a semantic
   candidate, **not a proven alias**. Do not apply SHB's O5 description to the
   numeric O5 object.
2. **T38 is ambiguous even as a display label.** API `T38` matches the manual's
   oxygen-on-primary-air enable setting. API `T110` is also displayed T38 but
   describes secondary-air oxygen influence. Match function and type, not only
   the display code.
3. **M6 is absent as an exact label.** The supplied sample has M6a
   (`duo.heizkreis.raumtempHysterese`) about a room thermostat, not SHB's M6
   residual-heat hysteresis. Missing does not mean zero, disabled, or writable
   via a guessed name.
4. **Profiles and suffixes are not universal.** The duplicated A2/A12/A62 rows
   reflect alternatives. The 2.7.4 initialization/assembly selects the `a` slope
   for circuit type ID 2, despite reversed circuit-2 help text. Do not select by
   help text alone; the live selector and both active flags must agree, and
   control effects on other firmware still need validation.
5. **Customer/installer/service is not HTTP authorization.** The SHB lists
   intended configuration levels, not a network security policy. Use the actual
   runtime metadata and code path for API behavior.
6. **Board numbering depends on version/configuration.** EHB 2024's HKM-0 example
   and SHB/API circuit labels are not a complete installation topology map.
7. **Reachability is not write validation.** A successful GET and a manual row
   establish neither accepted JSON encoding nor a safe value. The analyzed
   setter can reject an out-of-range float despite HTTP 200, and batches can
   partially apply. Read back the stored value and validate effects explicitly.

## 9. Consequences for a reusable library / Home Assistant integration

- Key settings by device identity plus **API `name`**. Keep display labels,
  manual-menu references, type, units, and translations as separate metadata.
- Start with read-only observation. Curated customer room and DHW targets can
  become opt-in numeric controls after verification; ignition, safety limits,
  pump/mixer configuration, resets, and regulator tuning should not be exposed
  merely because they are reachable or marked USER.
- A room setpoint alone does not justify a full `climate` entity. The zone can
  be weather compensated and affected by averaging, priority, setback,
  external lockouts, heat availability, and frost/overtemperature logic.
- Keep measured room temperature, comfort target, setback target, flow target,
  outdoor shutdown threshold, and room adjustment as distinct concepts.
- Treat schedules as structured controller programs; do not conflate E12's
  day/night split with per-zone heating windows or cylinder circulation.
- Use ordinary `daqOnly=true` sessions; customer parameter access does not
  require a SERVICE login. Neither the manuals nor this guide guarantees that
  every parameter write is mode-neutral or free of physical side effects.
- Native Home Assistant presentation/history and the planned library safety
  policy remain as described in the [development plan](client-development-plan.md).

No live requests, writes, commissioning changes, or electrical work were
performed while building this guide. Unresolved version/profile/JSON semantics
remain **VERIFY** items, not recommendations to experiment on a running boiler.
