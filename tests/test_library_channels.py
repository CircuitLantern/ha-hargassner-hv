# SPDX-License-Identifier: Apache-2.0

"""Explicit offline channel decoding preserves raw snapshots and write safety."""

import dataclasses
import unittest
from decimal import Decimal
from enum import IntEnum

from hvclient import (
    CHANNEL_MEANINGS,
    BoilerState,
    ChannelMeaning,
    DoorContactState,
    HeatingCircuitMode,
    HeatingCircuitRegulationState,
    ValidationError,
    decode_channel_value,
    get_channel_meaning,
)
from hvclient.models import channels_from_json
from hvclient.modes import mode_id


class OtherState(IntEnum):
    OFF = 0


BOILER = [
    (BoilerState.OFF, "off / ready"),
    (BoilerState.IGNITION_PREPARATION, "ignition preparation"),
    (BoilerState.IGNITION_MONITORING, "ignition monitoring"),
    (BoilerState.IGNITION_PAUSE, "ignition pause"),
    (BoilerState.TRANSITION_TO_POWER_BURNING, "transition to power burning"),
    (BoilerState.STARTUP, "startup / heating up"),
    (BoilerState.POWER_BURNING, "power burning"),
    (BoilerState.EMBER_MAINTENANCE, "ember maintenance"),
    (BoilerState.BURNOUT, "burnout"),
    (BoilerState.DE_ASHING, "de-ashing"),
    (BoilerState.RESIDUAL_HEAT, "residual heat"),
    (BoilerState.MANUAL, "manual"),
    (BoilerState.OVERTEMPERATURE, "overtemperature"),
    (BoilerState.EMBER_MAINTENANCE_LOCKOUT, "ember-maintenance lockout"),
]
MODES = [
    (HeatingCircuitMode.OFF, "off"), (HeatingCircuitMode.AUTOMATIC, "automatic"),
    (HeatingCircuitMode.PERMANENT_REDUCED, "continuous reduced-temperature operation"),
    (HeatingCircuitMode.PERMANENT_COMFORT, "continuous comfort heating"),
    (HeatingCircuitMode.ONCE_COMFORT, "one-time comfort heating"),
    (HeatingCircuitMode.ONCE_REDUCED, "one-time reduced-temperature operation"),
]
REGULATION = [
    (HeatingCircuitRegulationState.OFF, "off"),
    (HeatingCircuitRegulationState.COMFORT, "comfort heating"),
    (HeatingCircuitRegulationState.REDUCED, "reduced-temperature operation"),
    (HeatingCircuitRegulationState.FROST_PROTECTION, "frost protection"),
    (HeatingCircuitRegulationState.DAYTIME_REDUCTION_SHUTDOWN, "off above daytime-reduction outdoor-temperature limit"),
    (HeatingCircuitRegulationState.NIGHTTIME_REDUCTION_SHUTDOWN, "off above nighttime-reduction outdoor-temperature limit"),
    (HeatingCircuitRegulationState.OUTDOOR_TEMPERATURE_SHUTDOWN, "off above general outdoor-temperature heating limit"),
    (HeatingCircuitRegulationState.OVERTEMPERATURE, "overtemperature / heat dissipation"),
    (HeatingCircuitRegulationState.CHIMNEY_SWEEP, "chimney-sweep / emissions-test operation"),
    (HeatingCircuitRegulationState.RESIDUAL_HEAT, "residual-heat utilization"),
]
DOOR = [(DoorContactState.OPEN, "open"), (DoorContactState.CLOSED, "closed")]
CASES = {
    "KesselZustand": BOILER,
    "tuerkontakt": DOOR,
    **{f"Hk{zone}Status": MODES for zone in ("1", "2", "A")},
    **{f"Hk{zone}Zustand": REGULATION for zone in ("1", "2", "A")},
}


class ChannelMeaningTests(unittest.TestCase):
    def test_every_code_has_the_identified_enum_and_label(self):
        self.assertEqual(set(CHANNEL_MEANINGS), set(CASES))
        for name, cases in CASES.items():
            meaning = get_channel_meaning(name)
            self.assertIsInstance(meaning, ChannelMeaning)
            self.assertEqual(meaning.name, name)
            self.assertEqual(len(meaning.labels), len(cases))
            self.assertEqual(list(meaning.enum_type), [member for member, _ in cases])
            for code, (member, label) in enumerate(cases):
                with self.subTest(name=name, code=code):
                    self.assertEqual(int(member), code)
                    self.assertEqual(meaning.labels[member], label)
                    self.assertIs(meaning.decode(code), member)
                    self.assertIs(decode_channel_value(name, float(code)), member)
                    self.assertIs(decode_channel_value(name, member), member)

    def test_invalid_values_and_unknown_codes_are_not_coerced(self):
        for name, cases in CASES.items():
            for value in (None, True, False, "0", "off", "AUTO", 0.5, -0.5,
                          float("nan"), float("inf"), -float("inf"), Decimal(0),
                          {}, [], {"id": 0}, OtherState.OFF, -1, len(cases), 253,
                          10**500, 1e300):
                with self.subTest(name=name, value=value):
                    self.assertIsNone(decode_channel_value(name, value))

    def test_overlapping_enum_families_cannot_be_confused(self):
        families = (BoilerState, HeatingCircuitMode, HeatingCircuitRegulationState, DoorContactState)
        for name in CASES:
            meaning = get_channel_meaning(name)
            for family in families:
                with self.subTest(name=name, family=family):
                    if family is meaning.enum_type:
                        self.assertIs(decode_channel_value(name, family(0)), family(0))
                    else:
                        self.assertIsNone(decode_channel_value(name, family(0)))

    def test_exact_channel_names_only(self):
        for name in ("", "HkXStatus", "HkXZustand", "Hk3Status", "Hk3Zustand", "hk1Status",
                     "Hk1status", "Kesselzustand", "Boiler1Status", "Boiler1Zustand",
                     "Wahlschalter", "Stoerung", "tuerkontakt "):
            with self.subTest(name=name):
                self.assertIsNone(get_channel_meaning(name))
                self.assertIsNone(decode_channel_value(name, 0))

    def test_registry_records_and_labels_are_immutable(self):
        with self.assertRaises(TypeError):
            CHANNEL_MEANINGS["Other"] = get_channel_meaning("KesselZustand")
        for meaning in CHANNEL_MEANINGS.values():
            with self.subTest(name=meaning.name):
                with self.assertRaises(TypeError):
                    meaning.labels[0] = "changed"
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    meaning.label = "changed"
        original = get_channel_meaning("KesselZustand")
        labels = {0: "off"}
        custom = dataclasses.replace(original, labels=labels)
        labels[0] = "changed"
        self.assertEqual(custom.labels[0], "off")

    def test_evidence_does_not_claim_universal_compatibility(self):
        for name, meaning in CHANNEL_MEANINGS.items():
            with self.subTest(name=name):
                if name == "tuerkontakt":
                    self.assertEqual(meaning.evidence, "live_observation")
                    self.assertIsNone(meaning.verified_firmware)
                    self.assertIn("2026-11-20", meaning.description)
                    self.assertIn("broken cable", meaning.description)
                    self.assertIn("No independent", meaning.description)
                else:
                    self.assertEqual(meaning.evidence, "firmware")
                    self.assertEqual(meaning.verified_firmware, "2.7.4")

    def test_raw_snapshots_remain_numeric_detached_and_unmodified(self):
        values = {"KesselZustand": 6.0, "Hk1Status": 1, "Hk1Zustand": 6,
                  "Hk2Status": 0, "Hk2Zustand": 3, "tuerkontakt": 0, "future": 253}
        snapshot = channels_from_json(values)
        before = snapshot.to_dict()
        for name, raw in snapshot.items():
            decode_channel_value(name, raw)
        self.assertEqual(snapshot.to_dict(), before)
        self.assertIs(type(snapshot["KesselZustand"]), float)
        self.assertIs(type(snapshot["Hk1Status"]), int)
        self.assertEqual(snapshot["future"], 253)
        self.assertIs(decode_channel_value("KesselZustand", snapshot["KesselZustand"]), BoilerState.POWER_BURNING)
        self.assertIs(decode_channel_value("Hk1Status", snapshot["Hk1Status"]), HeatingCircuitMode.AUTOMATIC)
        self.assertIs(decode_channel_value("Hk1Zustand", snapshot["Hk1Zustand"]),
                      HeatingCircuitRegulationState.OUTDOOR_TEMPERATURE_SHUTDOWN)
        copy = snapshot.to_dict()
        copy["KesselZustand"] = -1
        self.assertEqual(snapshot["KesselZustand"], 6.0)

    def test_read_helpers_do_not_relax_mode_write_validation(self):
        self.assertIs(decode_channel_value("Hk1Status", 1.0), HeatingCircuitMode.AUTOMATIC)
        for value in (1.0, BoilerState.OFF, HeatingCircuitRegulationState.OFF, DoorContactState.OPEN):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                mode_id(value)
        self.assertEqual(mode_id(HeatingCircuitMode.AUTOMATIC), 1)
