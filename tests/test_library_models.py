import dataclasses
import unittest
from decimal import Decimal, localcontext

from hvclient import (
    NUMERIC_USER_WRITES,
    Choice,
    ControllerDateTime,
    ParameterAccessError,
    ProtocolError,
    TimeRange,
    ValidationError,
    WeeklyTimeRanges,
    WritePolicy,
    WritePolicyError,
)
from hvclient.models import parameter_from_json
from hvclient.policy import validate_change
from tests.library_support import numeric, user_catalog


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
