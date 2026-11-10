# SPDX-License-Identifier: MPL-2.0

import dataclasses
import unittest
from decimal import Decimal, localcontext
from enum import IntEnum

from hvclient import (
    CIRCUIT_MODE_PARAMETERS,
    CIRCUIT_MODE_WRITES,
    HEATING_CURVE_WRITES,
    NUMERIC_USER_WRITES,
    NUMERIC_WRITES,
    PARAMETER_WRITES,
    ChannelSnapshot,
    Choice,
    ControllerDateTime,
    HeatingCircuitMode,
    ParameterAccessError,
    ParameterMeaning,
    ProtocolError,
    TimeRange,
    ValidationError,
    WeeklyTimeRanges,
    WritePolicy,
    WritePolicyError,
    get_parameter_meaning,
)
from hvclient.models import (
    channels_from_json,
    parameter_from_json,
    pcb_info_from_json,
    system_info_from_json,
)
from hvclient.modes import mode_id
from hvclient.policy import (
    validate_change,
    validate_curve_context,
    validate_mode_context,
    wire_value,
)
from tests.library_support import curve_catalog, mode_catalog, numeric, user_catalog


class ModelTests(unittest.TestCase):
    def test_all_44_synthetic_user_values_decode(self):
        catalog = user_catalog()
        parameters = {name: parameter_from_json(row) for name, row in catalog.items()}
        self.assertEqual(len(parameters), 44)
        self.assertIs(type(parameters["4"].value), float)
        self.assertIs(type(parameters["2"].value), int)
        self.assertIs(parameters["Setup5"].value, False)
        self.assertEqual(parameters["Setup4a"].value, "")
        self.assertEqual(parameters["Setup4"].value, Choice(0, "First"))
        self.assertEqual(parameters["Setup8"].value, (Choice(0, "First"), Choice(1, "Second")))
        self.assertEqual(parameters["3"].value[0], TimeRange("00:00:00", "24:00:00"))
        weekly = parameters["3a"].value
        self.assertIsInstance(weekly[0], WeeklyTimeRanges)
        self.assertEqual(weekly[0].weekdays, (1, 2, 3, 4, 5, 6, 7))
        self.assertEqual(weekly[1].weekdays, ())
        self.assertEqual(weekly[1].time_ranges, (TimeRange("00:00:00", "00:00:00"),))
        self.assertEqual(parameters["Setup6"].value, ControllerDateTime(0, 0, 0, -1, -1, -1))

    def test_immutable_models_and_detached_wire_copy(self):
        row = numeric(extra={"nested": [1, 2]})
        parameter = parameter_from_json(row)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parameter.value = 25
        with self.assertRaises(TypeError):
            parameter.raw["value"] = 25
        with self.assertRaises(TypeError):
            parameter.raw["extra"]["nested"][0] = 9
        row["extra"]["nested"][0] = 9
        self.assertEqual(parameter.to_dict()["extra"], {"nested": [1, 2]})
        detached = parameter.to_dict()
        detached["extra"]["nested"][0] = 8
        self.assertEqual(parameter.to_dict()["extra"], {"nested": [1, 2]})

    def test_unknown_user_type_preserved_read_only(self):
        parameter = parameter_from_json(numeric(type="FUTURE_TYPE", value={"items": [False, 0]}))
        self.assertEqual(parameter.value["items"], (False, 0))
        self.assertEqual(parameter.to_dict()["value"], {"items": [False, 0]})
        with self.assertRaises(WritePolicyError):
            validate_change(parameter, 21, WritePolicy.only("4"))

    def test_boolean_labels_and_choices_preserved(self):
        parameter = parameter_from_json(numeric(
            type="BOOLEAN", value=False, trueText="Manual", falseText="Internet"
        ))
        self.assertEqual(parameter.true_text, "Manual")
        self.assertEqual(parameter.false_text, "Internet")
        parameter = parameter_from_json(user_catalog()["Setup4"])
        self.assertEqual(parameter.choices, (Choice(0, "First"), Choice(1, "Second")))

    def test_invalid_scalar_values_are_not_coerced(self):
        for type_name, value in (("INTEGER", True), ("INTEGER", 1.0), ("FLOAT", False),
                                 ("FLOAT", float("inf")), ("TEXT", 1), ("BOOLEAN", 0)):
            with self.subTest(type=type_name, value=value), self.assertRaises(ProtocolError):
                parameter_from_json(numeric(type=type_name, value=value))

    def test_missing_and_malformed_metadata(self):
        for changes in ({"active": 1}, {"minimum": False}, {"maximum": "30"},
                        {"choices": {}}, {"displayName": 1}, {"increment": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ProtocolError):
                parameter_from_json(numeric(**changes))
        for key in ("name", "type", "value"):
            row = numeric()
            del row[key]
            with self.subTest(missing=key), self.assertRaises(ProtocolError):
                parameter_from_json(row)

    def test_malformed_structured_values_fail_without_invented_defaults(self):
        cases = (
            ("CHOICE", {"id": True, "name": "x"}), ("MULTIPLE_CHOICE", {}),
            ("DAILY_CLOCK_RANGE", [{"start": "00:00:00"}]),
            ("WEEKLY_CLOCK_RANGE", [{"weekdays": [True], "time_ranges": []}]),
            ("DATETIME", {"year": 2026}),
        )
        for type_name, value in cases:
            with self.subTest(type=type_name), self.assertRaises(ProtocolError):
                parameter_from_json(numeric(type=type_name, value=value))

    def test_non_user_cannot_be_constructed_through_parser(self):
        for level in (None, "PLUMBER", "INSTALLER", "SERVICE", "DEVELOPER", "user"):
            with self.subTest(level=level), self.assertRaises(ParameterAccessError):
                parameter_from_json(numeric(securityLevel=level))


class PolicyTests(unittest.TestCase):
    def test_deny_by_default_and_explicit_subset(self):
        self.assertEqual(WritePolicy().allowed_names, frozenset())
        policy = WritePolicy.only("4", "5")
        self.assertEqual(policy.allowed_names, {"4", "5"})
        self.assertEqual(len(NUMERIC_USER_WRITES), 11)
        with self.assertRaises(WritePolicyError):
            policy.require("7")
        for names in ("4", {"E18"}, {"installer"}, {4}, None):
            with self.subTest(names=names), self.assertRaises(WritePolicyError):
                WritePolicy(names)

    def test_policy_copies_mutable_input(self):
        names = {"4"}
        policy = WritePolicy(names)
        names.add("E18")
        self.assertEqual(policy.allowed_names, {"4"})
        with self.assertRaises(TypeError):
            NUMERIC_USER_WRITES["E18"] = "BOOLEAN"

    def test_range_increment_and_no_silent_clamping(self):
        parameter = parameter_from_json(numeric())
        for value in (-0.5, 30.5, 20.25, "21", False, None, [], {},
                      float("nan"), float("inf"), Decimal("1e-1000000000")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_change(parameter, value, WritePolicy.only("4"))
        self.assertEqual(validate_change(parameter, 0, WritePolicy.only("4")), 0)
        self.assertEqual(validate_change(parameter, Decimal("20.5"), WritePolicy.only("4")), 20.5)

    def test_exact_grid_not_affected_by_decimal_context(self):
        parameter = parameter_from_json(numeric(minimum=0.1, maximum=3.1, increment=0.2))
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(validate_change(parameter, Decimal("0.3"), WritePolicy.only("4")), 0.3)
            with self.assertRaises(ValidationError):
                validate_change(parameter, Decimal("0.30000000000001"), WritePolicy.only("4"))
        with self.assertRaises(ValidationError):
            validate_change(parameter, Decimal("0.2"), WritePolicy.only("4"))

    def test_integer_type_is_strict(self):
        parameter = parameter_from_json(numeric("2", "INTEGER", 60, maximum=75, increment=1))
        self.assertEqual(validate_change(parameter, 0, WritePolicy.only("2")), 0)
        for value in (False, 60.0, Decimal(60)):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_change(parameter, value, WritePolicy.only("2"))

    def test_live_metadata_required_for_writes(self):
        for changes in ({"active": False}, {"active": None}, {"unit": "F"},
                        {"minimum": None}, {"maximum": None}, {"increment": None},
                        {"minimum": 31}, {"increment": 0}, {"increment": -1}):
            parameter = parameter_from_json(numeric(**changes))
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_change(parameter, 21, WritePolicy.only("4"))


class HeatingCurvePolicyTests(unittest.TestCase):
    def test_exact_six_exceptions_are_separate_from_user_names(self):
        self.assertEqual(set(HEATING_CURVE_WRITES), {"A2", "A2a", "A12", "A12a", "A62", "A62a"})
        self.assertEqual(len(NUMERIC_USER_WRITES), 11)
        self.assertEqual(len(NUMERIC_WRITES), 17)
        self.assertTrue(set(NUMERIC_USER_WRITES).isdisjoint(HEATING_CURVE_WRITES))
        for name in HEATING_CURVE_WRITES:
            self.assertEqual(WritePolicy.only(name).allowed_names, {name})
        for name in ("A1", "A11", "A61", "A3", "A3a", "A4a", "A5", "K38"):
            with self.subTest(name=name), self.assertRaises(WritePolicyError):
                WritePolicy.only(name)

    def test_curve_parser_preserves_plumber_classification_and_dimensionless_unit(self):
        parameter = parameter_from_json(curve_catalog()["A2a"])
        self.assertEqual(parameter.security_level, "PLUMBER")
        self.assertEqual(parameter.to_dict()["securityLevel"], "PLUMBER")
        self.assertEqual(parameter.unit, "")
        self.assertEqual(parameter.value, 0.6)
        self.assertEqual(validate_change(parameter, Decimal("0.7"), WritePolicy.only("A2a")), 0.7)

    def test_curve_metadata_and_numeric_grid_are_not_celsius_policy(self):
        row = curve_catalog()["A2a"]
        for changes in ({"securityLevel": "USER"}, {"unit": "°C"}, {"unit": None},
                        {"active": False}, {"minimum": None}, {"increment": None}):
            parameter = parameter_from_json(dict(row, **changes))
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_change(parameter, 0.7, WritePolicy.only("A2a"))
        parameter = parameter_from_json(row)
        for value in (0, 3.6, 0.65, False, float("nan"), "0.7"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_change(parameter, value, WritePolicy.only("A2a"))

    def test_profile_validation_uses_ids_and_active_pair_not_translated_help(self):
        rows = curve_catalog()
        active = parameter_from_json(rows["A12a"])
        inactive = parameter_from_json(rows["A12"])
        validate_curve_context(active, inactive, rows["A11"])
        with self.assertRaises(ValidationError):
            validate_curve_context(inactive, active, rows["A11"])
        for changes in ({"active": True}, {"unit": "°C"}, {"securityLevel": "USER"}):
            peer = parameter_from_json(dict(rows["A12"], **changes))
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_curve_context(active, peer, rows["A11"])
        for choice in (0, 1, 3, True, "2"):
            selector = dict(rows["A11"], value={"id": choice})
            with self.subTest(choice=choice), self.assertRaises(ValidationError):
                validate_curve_context(active, inactive, selector)


class CircuitModePolicyTests(unittest.TestCase):
    def test_exact_three_mode_exceptions_and_six_ids(self):
        self.assertEqual(set(CIRCUIT_MODE_WRITES), {"A1z", "A11z", "A61z"})
        self.assertEqual([int(mode) for mode in HeatingCircuitMode], list(range(6)))
        self.assertEqual(len(NUMERIC_USER_WRITES), 11)
        self.assertEqual(len(NUMERIC_WRITES), 17)
        self.assertEqual(len(PARAMETER_WRITES), 26)
        self.assertEqual(WritePolicy.only("A1z").allowed_names, {"A1z"})
        for name in ("system.mode", "Wahlschalter", "A1", "developer", "E18", "Setup4"):
            with self.subTest(name=name), self.assertRaises(WritePolicyError):
                WritePolicy.only(name)

    def test_mode_ids_are_explicit_and_never_plant_service(self):
        class PlantMode(IntEnum):
            OTHER = 1
            SERVICE = 6

        self.assertEqual(mode_id(HeatingCircuitMode.OFF), 0)
        self.assertEqual(mode_id(HeatingCircuitMode.AUTOMATIC), 1)
        for value in (True, False, -1, 6, 100, 1.0, Decimal(1), "Automatic", "1",
                      {"id": 1}, Choice(1, "Automatic"), PlantMode.OTHER, PlantMode.SERVICE):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                mode_id(value)

    def test_hidden_mode_exception_preserves_level_and_encodes_id_only(self):
        parameter = parameter_from_json(mode_catalog()["A1z"])
        self.assertEqual(parameter.security_level, "DEVELOPER")
        self.assertIs(parameter.active, False)
        self.assertEqual(parameter.value, Choice(3, "Comfort"))
        value = validate_change(parameter, HeatingCircuitMode.AUTOMATIC, WritePolicy.only("A1z"))
        self.assertIs(type(value), int)
        self.assertEqual(wire_value("A1z", value), {"id": 1})
        self.assertEqual(wire_value("A1z", 0), {"id": 0})
        self.assertEqual(validate_mode_context(parameter, mode_catalog()["A1"]), 2)

    def test_hidden_flag_does_not_allow_arbitrary_inactive_or_developer_writes(self):
        row = mode_catalog()["A1z"]
        for changes in ({"securityLevel": "USER"}, {"active": True}, {"active": None},
                        {"unit": "°C"}, {"choices": []}):
            parameter = parameter_from_json(dict(row, **changes))
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_change(parameter, 1, WritePolicy.only("A1z"))
        with self.assertRaises(ValidationError):
            validate_change(parameter_from_json(numeric(active=False)), 21, WritePolicy.only("4"))
        with self.assertRaises(ParameterAccessError):
            parameter_from_json(dict(row, name="arbitrary-developer-choice"))

    def test_mode_choice_contract_rejects_missing_extra_duplicate_or_unknown_current_ids(self):
        row = mode_catalog()["A1z"]
        for choices in (row["choices"][:-1], row["choices"] + [{"id": 6, "name": "Unexpected"}],
                        row["choices"][:-1] + [row["choices"][0]]):
            parameter = parameter_from_json(dict(row, choices=choices))
            with self.subTest(choices=choices), self.assertRaises(ValidationError):
                validate_change(parameter, 1, WritePolicy.only("A1z"))
        parameter = parameter_from_json(dict(row, value={"id": 6, "name": "Unknown"}))
        with self.assertRaises(ValidationError):
            validate_change(parameter, 0, WritePolicy.only("A1z"))

    def test_mode_context_requires_present_configured_circuit_and_stable_type(self):
        rows = mode_catalog()
        parameter = parameter_from_json(rows["A1z"])
        for changes in ({"active": False}, {"securityLevel": "DEVELOPER"},
                        {"value": {"id": 0}}, {"value": {"id": True}}, {"value": {"id": 3}}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                validate_mode_context(parameter, dict(rows["A1"], **changes))
        with self.assertRaises(ValidationError):
            validate_mode_context(parameter, rows["A1"], expected_type=1)


class ParameterMeaningTests(unittest.TestCase):
    def test_public_zone_registry_resolves_all_three_wire_names_and_meanings(self):
        self.assertEqual(set(CIRCUIT_MODE_PARAMETERS), {"1", "2", "A"})
        expected = {"1": "A1z", "2": "A11z", "A": "A61z"}
        for zone, name in expected.items():
            with self.subTest(zone=zone):
                meaning = CIRCUIT_MODE_PARAMETERS[zone]
                self.assertIsInstance(meaning, ParameterMeaning)
                self.assertEqual(meaning.name, name)
                self.assertEqual(meaning.zone, zone)
                self.assertEqual(meaning.purpose, "operating_mode")
                self.assertEqual(meaning.label, f"Heating circuit {zone} operating mode")
                self.assertIn("space-heating circuit", meaning.description)
                self.assertIs(get_parameter_meaning(name), meaning)
        self.assertEqual({p.name for p in CIRCUIT_MODE_PARAMETERS.values()}, set(CIRCUIT_MODE_WRITES))

    def test_returned_parameter_attaches_same_meaning_without_replacing_firmware_text(self):
        rows = mode_catalog()
        for zone, meaning in CIRCUIT_MODE_PARAMETERS.items():
            row = dict(rows[meaning.name], description="Mode de zone", helpText="Mode réglé")
            parameter = parameter_from_json(row)
            with self.subTest(zone=zone):
                self.assertIs(parameter.meaning, meaning)
                self.assertEqual(parameter.description, "Mode de zone")
                self.assertEqual(parameter.help_text, "Mode réglé")
                self.assertEqual(parameter.to_dict(), row)
                self.assertNotIn("meaning", parameter.to_dict())

    def test_registry_and_descriptors_are_immutable(self):
        meaning = CIRCUIT_MODE_PARAMETERS["1"]
        with self.assertRaises(TypeError):
            CIRCUIT_MODE_PARAMETERS["1"] = meaning
        with self.assertRaises(dataclasses.FrozenInstanceError):
            meaning.zone = "2"
        parameter = parameter_from_json(mode_catalog()[meaning.name])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parameter.meaning = None

    def test_unknown_and_unmapped_parameters_have_no_guessed_meaning(self):
        for name in ("unknown", "A99z", "A1", "4", "A2a"):
            with self.subTest(name=name):
                self.assertIsNone(get_parameter_meaning(name))
        self.assertIsNone(parameter_from_json(numeric()).meaning)

    def test_firmware_metadata_cannot_override_reviewed_library_meaning(self):
        row = dict(mode_catalog()["A1z"], meaning={"zone": "untrusted", "purpose": "other"})
        parameter = parameter_from_json(row)
        self.assertEqual(parameter.meaning.zone, "1")
        self.assertEqual(parameter.meaning.purpose, "operating_mode")
        self.assertEqual(parameter.to_dict()["meaning"], row["meaning"])


class SnapshotModelTests(unittest.TestCase):
    def test_channels_are_dynamic_numeric_mapping_without_unit_or_boolean_inference(self):
        document = {"future.channel": 0, "temperature": -12.5, "counter": 2**60 + 1}
        snapshot = channels_from_json(document)
        self.assertIsInstance(snapshot, ChannelSnapshot)
        self.assertEqual(len(snapshot), 3)
        self.assertEqual(set(snapshot), set(document))
        self.assertEqual(snapshot["future.channel"], 0)
        self.assertIs(type(snapshot["future.channel"]), int)
        self.assertEqual(snapshot["counter"], 2**60 + 1)
        self.assertEqual(dict(snapshot), document)
        self.assertEqual(snapshot, document)
        self.assertIsNone(snapshot.get("absent"))
        self.assertFalse(hasattr(snapshot, "unit"))

    def test_empty_channel_snapshot_is_valid(self):
        snapshot = channels_from_json({})
        self.assertEqual(len(snapshot), 0)
        self.assertEqual(snapshot.to_dict(), {})

    def test_channel_snapshot_is_immutable_and_detached(self):
        document = {"channel": 21.5}
        snapshot = channels_from_json(document)
        document["channel"] = 0
        detached = snapshot.to_dict()
        detached["channel"] = 0
        self.assertEqual(snapshot["channel"], 21.5)
        with self.assertRaises(TypeError):
            snapshot.channels["channel"] = 0
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.channels = {}

    def test_invalid_channel_shapes_names_and_values(self):
        for document in (None, [], "invalid", {"": 0}, {1: 0}):
            with self.subTest(document=document), self.assertRaises(ProtocolError):
                channels_from_json(document)
        for value in (True, False, None, "21", {}, [], float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                channels_from_json({"channel": value})

    def test_system_info_preserves_strings_and_unknown_fields_without_repr_identifiers(self):
        document = {
            "softwareVersion": "test_firmware_1.0", "mac": "private-test-mac",
            "systemId": "private-test-id", "commissionNumber": "private-test-commission",
            "articleSerialNumber": "private-test-serial", "extension": {"items": [1, False]},
        }
        info = system_info_from_json(document)
        self.assertEqual(info.software_version, document["softwareVersion"])
        self.assertEqual(info.mac, document["mac"])
        self.assertEqual(info.system_id, document["systemId"])
        self.assertEqual(info.commission_number, document["commissionNumber"])
        self.assertEqual(info.article_serial_number, document["articleSerialNumber"])
        self.assertEqual(info.to_dict(), document)
        self.assertNotIn("private-test", repr(info))
        self.assertNotIn("extension", repr(info))

    def test_system_info_missing_or_null_fields_do_not_generate_identity(self):
        for document in ({}, {"systemId": None}, {"softwareVersion": "future", "newKey": 3}):
            with self.subTest(document=document):
                info = system_info_from_json(document)
                self.assertIsNone(info.system_id)
                self.assertIsNone(info.mac)
                self.assertEqual(info.to_dict(), document)

    def test_system_info_is_immutable_and_copies_nested_metadata(self):
        document = {"systemId": "test", "future": {"items": [1]}}
        info = system_info_from_json(document)
        document["future"]["items"][0] = 2
        info.to_dict()["future"]["items"][0] = 3
        self.assertEqual(info.to_dict()["future"], {"items": [1]})
        with self.assertRaises(TypeError):
            info.raw["future"]["items"][0] = 4
        with self.assertRaises(dataclasses.FrozenInstanceError):
            info.system_id = "other"

    def test_system_info_rejects_bad_shapes_and_string_field_types_without_data_echo(self):
        for document in (None, [], "private-device-data"):
            with self.subTest(document=document), self.assertRaises(ProtocolError) as caught:
                system_info_from_json(document)
            self.assertNotIn("private-device-data", str(caught.exception))
        for key in ("mac", "systemId", "commissionNumber", "articleSerialNumber", "softwareVersion"):
            for value in (True, 123, [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(ProtocolError):
                    system_info_from_json({key: value})

    def test_pcb_numbers_and_unknown_fields_preserved_without_units_or_range_clamping(self):
        document = {"internalTemperature": -1.5, "internalVoltage": 0, "future": [1, 2]}
        pcb = pcb_info_from_json(document)
        self.assertEqual(pcb.internal_temperature, -1.5)
        self.assertEqual(pcb.internal_voltage, 0)
        self.assertIs(type(pcb.internal_voltage), int)
        self.assertEqual(pcb.to_dict(), document)
        document["future"][0] = 9
        pcb.to_dict()["future"][0] = 8
        self.assertEqual(pcb.to_dict()["future"], [1, 2])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            pcb.internal_voltage = 24

    def test_pcb_rejects_missing_invalid_and_nonfinite_readings(self):
        for document in (None, [], {}, {"internalTemperature": 20}, {"internalVoltage": 24}):
            with self.subTest(document=document), self.assertRaises(ProtocolError):
                pcb_info_from_json(document)
        for key in ("internalTemperature", "internalVoltage"):
            for value in (None, True, "24", float("nan"), float("inf")):
                document = {"internalTemperature": 20, "internalVoltage": 24, key: value}
                with self.subTest(key=key, value=value), self.assertRaises(ProtocolError):
                    pcb_info_from_json(document)

    def test_unknown_info_and_pcb_metadata_must_be_finite_json(self):
        with self.assertRaises(ProtocolError):
            system_info_from_json({"extension": float("inf")})
        with self.assertRaises(ProtocolError):
            pcb_info_from_json({"internalTemperature": 20, "internalVoltage": 24,
                                "extension": float("nan")})
