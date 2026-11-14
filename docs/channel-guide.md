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
