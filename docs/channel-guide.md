<!-- SPDX-License-Identifier: Apache-2.0 -->

# Operational channel interpretation

The HA adapter maintains an exact-name presentation map in
`custom_components/hargassner_hv/telemetry.py`. The SDK still returns the raw
numeric snapshot without guessing units or implementing a second transport.
Unknown channels remain optional diagnostics; channel names do not grant writes.

## Curated readings

| Channel keys | Interpretation | Native unit | Statistics |
|---|---|---|---|
| `kesselfuehler`, `ruecklauffuehler`, `rauchgasfuehler_nicrni` | Boiler water, return water, flue gas | °C | Measurement |
| `aussentemperatur`, `AussentempMittel` | Outdoor reading and controller average | °C | Measurement |
| `puffer_oben`, `puffer_mitte`, `puffer_unten` | Thermal-store top/middle/bottom temperatures | °C | Measurement |
| `HkmHk1Fuehler`, `HkmHk2Fuehler`, `HkaHkFuehler` | Heating-circuit 1/2/A flow temperatures | °C | Measurement |
| `HkmBoilerFuehler`, `HkaBoilerFuehler` | DHW-cylinder 1/A temperatures, not boiler-water temperature | °C | Measurement |
| `Hk1Fr35RaumTemp`, `Hk2Fr35RaumTemp`, `HkAFr35RaumTemp` | Reported room-unit temperatures; optional and potentially retained | °C | None |
| `TKsoll`, `RL_Soll`, `Hk1Soll`, `Hk2Soll`, `HkASoll` | Calculated boiler/return/circuit-flow targets | °C | None |
| `HeizkreisAnforderung`, `BoilerAnforderung` | Requested temperatures from heating circuits/DHW; not Boolean demand flags | °C | None |
| `Pufferfuellgrad` | Calculated buffer-charge indication, not physical liquid level | % | None |
| `Leistung` | Controller boiler-output indication, not measured kW | % | None |
| `lambda_O2` | Reported flue-gas oxygen | % | Measurement |
| `O2Soll` | Oxygen target | % | None |
| `saugzug_rpm`, `SzSollRPM` | Induced-draught fan feedback and target speed | rpm | Measurement for feedback only |

No value is rescaled from its name or magnitude. Native engineering units are
taken from reviewed firmware definitions; `UpM` is normalized to the equivalent
HA unit rpm. HA may convert native units according to the user's display
preferences. No channel is interpreted as accumulated energy or a power meter.
Room corrections such as `Hk1Fr35RaumTempKorr` are deliberately not treated as
actual room-temperature measurements.

## Evidence

Existing 2.7.4 C/assembly exports were reused; no firmware was re-decompiled
or vendor artifacts added to version control:

- `ChannelHandler::handle` (`0x221bac`) reads each channel's double value into
  a JSON number without unit conversion. The HTTP response omits channel
  unit strings, per-sensor validity, and sample timestamps.
- `CommonChannelProvider::addPt1000` (`0xc3414`), `addHainzlHkm` (`0xc2680`),
  and `addHainzlHka` (`0xc1e3c`) bind the listed physical temperature inputs
  to sensor channels. Temperature sensor unit getters identify °C: the
  binary string at `0x285a54` is the UTF-8 Celsius symbol.
- `CommonChannelProvider::getChannels` (`0xc4b0c`) explicitly registers the
  reviewed calculated temperature channels, room-unit temperatures, oxygen
  target, and fan target with Celsius/percent/rpm unit definitions.
- `PufferBase::internalInitializeChannels` (`0x158ed0`) registers the three
  buffer temperatures as °C and the buffer-charge indication as %.
- `HvChannelProvider::getChannels` (`0x30a50`) registers `Leistung` as %;
  `LambdaLeistungsregler::internalInitializeChannels` (`0x152e98`) registers
  `lambda_O2` as %. These are not inferred from a captured value.
- `MotorRpmChannel` constructors (`0xd3c1c`, `0xd37d8`) use `UpM`, the
  revolutions-per-minute unit stored at `0x287518`.
- `HeizkreisBase::refreshDaqChannels` (`0x1470dc`) reads the room-unit sensor
  when the room-unit function is selected; the alternate branch stores
  double −1.0. The critical default branch at `0x14727e` was checked in
  assembly rather than inferred from incomplete decompiler float arguments.
- `HainzlFr35TemperatureSensor::internalGetValue` (`0x2314e4`) can retain
  prior values when a unit disconnects. A successful HTTP snapshot alone
  does not establish a fresh physical room measurement.

Names have also been observed in 3.4.0 responses. That corroborates naming,
not calibration, sentinel compatibility, physical sensor installation, or
behavior on every deployed firmware. No new hardware requests were made
for this review, and no captured readings or device identities are published.

## Door-contact diagnostic

The optional diagnostic channel `tuerkontakt` is displayed as **Boiler door
contact**, an enum sensor with `open` and `closed` states:

| Raw channel value | Home Assistant state |
|---|---|
| `0` | `open` |
| `1` | `closed` |

**Polarity evidence:** on 2026-11-20, the user reported observing `0` with the
door open and `1` with it closed on a live Smart HV device. No independent source
confirming this exact HTTP channel's numeric polarity was found in the reviewed
manuals or public-source search. The mapping is based on that observation, not
an established contract across all firmware or installations; the firmware
version of this particular observation was not specified.

The manufacturer's [Smart-HV 17–23 operating manual](https://www.hargassner.com/wp-content/uploads/2022/08/ba-smart-hv-17-23-de.pdf)
(PDF page 25, digital-input test) describes green as contact closed and red as
contact interrupted. For the door contact, red can mean an open door,
disconnected plug, or broken cable. This corroborates the contact's purpose,
**not** the numeric HTTP polarity. Consequently, `open` is a reported contact
state, not proof that the physical door is open; `closed` is not a safety
certification. This entity must not be used as a safety interlock.

The sensor remains an optional diagnostic under the additional-diagnostic-channel
option, disabled by default for new entities. Existing entity IDs, custom names,
and enable/disable choices are retained. It has no unit or statistics class;
`channel` and `raw_value` attributes expose the source reading. Missing channels,
failed polls, or values other than exactly `0`/`1` are unavailable, not coerced
to open or closed. The SDK still returns the raw numeric value. No additional
endpoint, live request, or write permission is introduced.

## Boiler-state diagnostic

The optional **Boiler state** enum sensor decodes `KesselZustand` using the
integer-to-name table in Smart HV firmware **2.7.4**:

| Raw code | Firmware name | Home Assistant state |
|---|---|---|
| `0` | `AUS` | `off / ready` |
| `1` | `ZUENDVORBEREITUNG` | `ignition preparation` |
| `2` | `ZUENDUEBERWACHUNG` | `ignition monitoring` |
| `3` | `ZUENDUNG_PAUSE` | `ignition pause` |
| `4` | `UEBERGANG_LB` | `transition to power burning` |
| `5` | `ANHEIZEN` | `startup / heating up` |
| `6` | `LEISTUNGSBRAND` | `power burning` |
| `7` | `GLUTERHALTUNG` | `ember maintenance` |
| `8` | `AUSBRAND` | `burnout` |
| `9` | `ENTASCHUNG` | `de-ashing` |
| `10` | `RESTWAERME` | `residual heat` |
| `11` | `HAND` | `manual` |
| `12` | `UEBERTEMP` | `overtemperature` |
| `13` | `GLUTERHALTUNGSSPERRE` | `ember-maintenance lockout` |

The ordinary burn cycle is `0 → 2 → 5 → 6 → 8 → 10 → 0`; reloading, door
opening, and other conditions can interrupt it. The shared enum also defines
states that this Smart HV version may not normally use. `12` is explicitly
handled as overtemperature by its combustion logic. Enum membership alone is
not evidence that all fourteen states can occur on a particular installation.

**Firmware evidence:** `HvKessel::refreshDaqChannels` (`0x4334c`) copies the
boiler-state field at offset `0x50` directly into `KesselZustand` (the load is at
`0x43420`). `HvKessel::getKesselState` (`0x47b74`) reads the same field, and
`HvKessel::setKesselSSW` (`0x40474`) updates it with a `KesselState::Value`.
`KesselState::operator QString` (`0x26d514`) supplies the table above; its branch
table and label strings were checked against executable bytes, not just
generated C. `HvKessel::AblaufWork` (`0x43cf0`) corroborates the common state
transitions. No vendor binary, generated decompilation, or captures are bundled.

The manufacturer's [Smart-HV 17–23 operating manual](https://www.hargassner.com/wp-content/uploads/2022/08/ba-smart-hv-17-23-de.pdf),
section 6, PDF pages 20–21, explains the common states and their transitions,
but does not specify numeric IDs. Its optional electric-ignition states are not
assigned codes by assuming the manual's order matches the enum. The mapping is
confirmed for **2.7.4**, not independently verified across later firmware.

This is a combustion-state readout, **not** the `Wahlschalter` operating-mode
selector or a heating-circuit `Hk*Zustand` decoder. In particular, `manual` is
the name of enum code `11`, not a promise that manual operating mode reports
that code; `off / ready` does not certify the whole heating system is off.

The existing sensor identity, custom name, diagnostic category, and enable/disable
choices are preserved. New entities remain disabled by default and require the
additional-diagnostic-channel option. `channel` and `raw_value` attributes retain
the source name and numeric code while available (HA omits these attributes when
the entity is unavailable); the SDK still returns the raw number. Missing
channels, failed polls, and codes outside the exact defined integers (including
fractional readings) are unavailable rather than mapped to off or another state.
There is no unit or statistics class, no added polling endpoint, and no write.
Automations previously comparing the sensor state to a number need to use the
new text state or its `raw_value` attribute instead.

## Heating-circuit operating-mode and regulation-state diagnostics

For circuits **1**, **2**, and **A**, the optional enum sensors use exact channel
names `Hk1Status`/`Hk2Status`/`HkAStatus` and
`Hk1Zustand`/`Hk2Zustand`/`HkAZustand`. They are named **Heating circuit X
operating mode** and **Heating circuit X regulation state**, respectively.
No other channel names are decoded by this map.

### `HkXStatus`: current operating mode

| Raw code | Firmware name | Home Assistant state |
|---|---|---|
| `0` | `AUS` | `off` |
| `1` | `AUTO` | `automatic` |
| `2` | `DAUER_ABSENKEN` | `continuous reduced-temperature operation` |
| `3` | `DAUER_HEIZEN` | `continuous comfort heating` |
| `4` | `1xHEIZEN` | `one-time comfort heating` |
| `5` | `1xABSENKEN` | `one-time reduced-temperature operation` |

This is the current runtime mode, not simply the stored mode-setting parameter.
The controller owns one-time override expiry and return to Automatic, based on
heating-program conditions rather than a fixed-duration HA timer. The existing
climate entity already reads this same numeric runtime channel; the diagnostic
presentation does not change its control or expiry handling.

### `HkXZustand`: current regulation state

| Raw code | Home Assistant state | Interpretation |
|---|---|---|
| `0` | `off` | Circuit regulation is off |
| `1` | `comfort heating` | Comfort-temperature regulation |
| `2` | `reduced-temperature operation` | Reduced-temperature regulation |
| `3` | `frost protection` | Protective frost operation |
| `4` | `off above daytime-reduction outdoor-temperature limit` | Daytime-reduction shutdown threshold, HK-12 |
| `5` | `off above nighttime-reduction outdoor-temperature limit` | Nighttime-reduction shutdown threshold, HK-13 |
| `6` | `off above general outdoor-temperature heating limit` | General heating shutdown threshold, HK-11 |
| `7` | `overtemperature / heat dissipation` | Overtemperature regulation / heat disposal |
| `8` | `chimney-sweep / emissions-test operation` | Emissions-test regulation |
| `9` | `residual-heat utilization` | Utilization of remaining heat |

The two enums are independent: Status `1` with Zustand `2` means Automatic mode
in a reduced-temperature phase; Status `1` with Zustand `6` means Automatic
mode with heating off above its outdoor-temperature limit. Status `0` can
coexist with Zustand `3` because Off does not disable frost protection. These
states report controller regulation, not confirmed pump operation, valve
position, or physical heat delivery. No climate `hvac_action` is inferred.

**Evidence (firmware 2.7.4):** `HeizkreisBase::internalInitializeChannels`
(`0x145870`) binds Status channels at offset `0x358` and Zustand at `0x360`.
`HeizkreisBase::refreshDaqChannels` (`0x1470dc`) writes the current-mode field
(`0x13c`) to Status and the regulation-state field (`0x180`) to Zustand.
`HeizkreisState::operator QString` (`0x2405b0`) explicitly names Status codes;
despite the class name, this enum is the operating mode. Its branch table and
strings were verified against executable bytes.

Zustand uses the separate `Heizkreis::HeizkreisZustand` enum, set through
`HeizkreisBase::setHKZustand` (`0x146768`). Named functions establish codes:
`Heizen` (`0x148d1c`) → 1, `Absenken` (`0x148d50`) → 2, `Frostschutz`
(`0x148974`) → 3, `Uebertemp` (`0x1487f0`) → 7, `AblaufChimneySweeper`
(`0x148714`) → 8, and `Restwaerme` (`0x1488d4`) → 9.
`AblaufWork` (`0x148d84`) calls `Aus` (`0x146d84`) with 0 or with shutdown
codes 4/5/6, tied respectively to parameters 12/13/11. Critical assignments
were checked in assembly; these English regulation labels describe code
behavior rather than claiming an explicit firmware string-conversion table.
The manufacturer's [Smart-HV 17–23 operating manual](https://www.hargassner.com/wp-content/uploads/2022/08/ba-smart-hv-17-23-de.pdf),
PDF page 27, corroborates the shutdown-threshold meanings but supplies no
channel code IDs. This evidence is confirmed for 2.7.4, not independently
validated across every later firmware.

Both diagnostic sensors preserve their previous raw entity IDs, custom names,
diagnostic category, and enable/disable preferences. New entities stay disabled
by default under the additional-diagnostic-channel option. Exact integer-valued
readings (including equivalent floats) map to text; missing channels, failed
polls, fractional readings, and undefined codes are unavailable. `channel`
and `raw_value` attributes expose source readings while available; HA omits
them when unavailable. There are no units or statistics classes. Numeric-state
automations must use the new text states or `raw_value` attribute. SDK values
remain numeric, and no endpoints, polling schedules, writes, regulation,
or controller-owned expiry behavior change.

## Availability and migration

The known room-unit −1 sentinel is treated as unavailable, conservatively
losing a genuine sample of exactly −1 °C because the flat API cannot distinguish
the cases. Zero remains a valid numeric reading. No other sentinel is guessed
from an unusual value. Missing/failed channels are unavailable, not invented
zeros; in-range or retained values are still only controller reports.

Circuit/DHW parameter applicability helps choose initial enabled defaults,
not certify installed hardware or invalidate a manually enabled observation.
Unclassified channels keep their raw values and diagnostic category. The HA
upgrade reuses existing IDs and preserves explicit user preferences; it does
not change the boiler, enable writes, restore setpoints, or initiate SERVICE mode.
