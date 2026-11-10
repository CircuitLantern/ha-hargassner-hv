# SPDX-License-Identifier: MPL-2.0

import dataclasses
import unittest
from decimal import Decimal
from unittest.mock import patch

from hvclient import (
    CIRCUIT_SCHEDULE_PARAMETERS,
    CIRCUIT_SCHEDULE_WRITES,
    DAILY_SCHEDULE_LIMITS,
    PARAMETER_WRITES,
    WEEKLY_SCHEDULE_LIMITS,
    DailySchedule,
    HeatingCircuitMode,
    ScheduleLimits,
    TimeRange,
    ValidationError,
    Weekday,
    WeeklySchedule,
    WeeklyTimeRanges,
    WritePolicy,
    WritePolicyError,
    get_parameter_meaning,
    get_schedule_limits,
)
from hvclient.models import parameter_from_json
from hvclient.policy import (
    validate_change,
    validate_schedule_context,
    validate_schedule_parameter,
)
from hvclient.schedules import normalize_schedule, schedules_equal
from tests.library_support import schedule_catalog


class ScheduleTests(unittest.TestCase):
    def test_exact_program_names_and_public_meanings(self):
        self.assertEqual(len(PARAMETER_WRITES), 26)
        self.assertEqual(set(CIRCUIT_SCHEDULE_WRITES), {"3", "3a", "6", "6a", "HP3", "HP3a"})
        for zone, daily, weekly in (("1", "3", "3a"), ("2", "6", "6a"), ("A", "HP3", "HP3a")):
            programs = CIRCUIT_SCHEDULE_PARAMETERS[zone]
            self.assertEqual((programs.daily.name, programs.weekly.name), (daily, weekly))
            for name in (daily, weekly):
                self.assertEqual(get_parameter_meaning(name).zone, zone)
                self.assertEqual(get_parameter_meaning(name).purpose, "comfort_schedule")
                self.assertEqual(WritePolicy.only(name).allowed_names, {name})
        for name in ("D9", "D5", "1", "1a", "HP1", "HP1a", "fws.freigabe.wochenuhr"):
            with self.subTest(name=name), self.assertRaises(WritePolicyError):
                WritePolicy.only(name)

    def test_weekday_ids_match_qdate_iso_week(self):
        self.assertEqual([int(day) for day in Weekday], list(range(1, 8)))
        self.assertEqual(Weekday.MONDAY, 1)
        self.assertEqual(Weekday.SUNDAY, 7)

    def test_daily_normalizes_hhmm_and_pads_reviewed_two_slots(self):
        value = normalize_schedule("3", DailySchedule((TimeRange("06:00", "09:00"),)))
        self.assertEqual(value.to_json(), [
            {"start": "06:00:00", "end": "09:00:00"},
            {"start": "00:00:00", "end": "00:00:00"},
        ])
        self.assertEqual(normalize_schedule("3", []).to_json(), [
            {"start": "00:00:00", "end": "00:00:00"}
        ] * 2)
        self.assertEqual(normalize_schedule("3", [TimeRange("00:00", "24:00")]).to_json()[0],
                         {"start": "00:00:00", "end": "24:00:00"})

    def test_weekly_encodes_day_groups_and_no_disabled_zero_length_ranges(self):
        schedule = WeeklySchedule((
            WeeklyTimeRanges(tuple(Weekday)[:5], (TimeRange("06:00", "09:00"),)),
            WeeklyTimeRanges((Weekday.SATURDAY, Weekday.SUNDAY), ()),
        ))
        value = normalize_schedule("3a", schedule)
        self.assertEqual(value.to_json(), [
            {"weekdays": [1, 2, 3, 4, 5], "time_ranges": [{"start": "06:00:00", "end": "09:00:00"}]},
            {"weekdays": [6, 7], "time_ranges": []},
        ])
        raw = schedule_catalog(weekly=True)["3a"]["value"]
        self.assertEqual(len(normalize_schedule("3a", raw).to_json()[0]["time_ranges"]), 1)

    def test_equal_programs_ignore_group_order_padding_and_adjacent_window_shape(self):
        current = schedule_catalog(weekly=True)["3a"]["value"]
        reordered = list(reversed(current))
        self.assertTrue(schedules_equal("3a", current, reordered))
        self.assertTrue(schedules_equal("3", [TimeRange("06:00", "09:00")],
                                       [TimeRange("06:00", "08:00"), TimeRange("08:00", "09:00")]))
        self.assertFalse(schedules_equal("3", [TimeRange("06:00", "09:00")],
                                        [TimeRange("06:00", "10:00")]))
        same = [{"weekdays": [1, 2, 3, 4, 5, 6, 7], "time_ranges": []}]
        self.assertTrue(schedules_equal("3a", same, [{"weekdays": [], "time_ranges": []}]))

    def test_invalid_times_and_overnight_or_overlapping_ranges_rejected(self):
        for start, end in (("6:00", "09:00"), ("24:00", "24:00"), ("12:60", "14:00"),
                           ("06:00:01", "09:00"), ("06:00", "24:01"),
                           ("22:00", "06:00"), ("09:00", "09:00")):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                normalize_schedule("3", [TimeRange(start, end)])
        for value in (
            [TimeRange("06:00", "09:00"), TimeRange("08:00", "10:00")],
            [TimeRange("06:00", "07:00")] * 3,
            [{"from": "06:00", "to": "09:00"}], True, "06:00-09:00",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_schedule("3", value)

    def test_weekly_requires_full_explicit_nonoverlapping_day_coverage_and_two_groups(self):
        for days in ([0], [8], [True], [Decimal(1)], [HeatingCircuitMode.AUTOMATIC], [1, 1]):
            with self.subTest(days=days), self.assertRaises(ValidationError):
                normalize_schedule("3a", [{"weekdays": days, "time_ranges": []}])
        for groups in (
            [{"weekdays": [1, 2, 3, 4, 5], "time_ranges": []}],
            [{"weekdays": [1, 2, 3, 4, 5], "time_ranges": []},
             {"weekdays": [5, 6, 7], "time_ranges": []}],
            [{"weekdays": [1, 2], "time_ranges": []}] * 3,
            [],
        ):
            with self.subTest(groups=groups), self.assertRaises(ValidationError):
                normalize_schedule("3a", groups)

    def test_live_context_requires_selected_clock_profile_and_active_configured_circuit(self):
        for weekly, name, peer in ((False, "3", "3a"), (True, "3a", "3")):
            rows = schedule_catalog(weekly=weekly)
            target, other = parameter_from_json(rows[name]), parameter_from_json(rows[peer])
            self.assertEqual(validate_schedule_context(target, other, rows["A1"], rows["D9"]), 2)
            value = validate_change(target, target.value, WritePolicy.only(name))
            self.assertTrue(schedules_equal(name, target.value, value))
            with self.assertRaises(ValidationError):
                validate_schedule_context(target, other, rows["A1"], dict(rows["D9"], value=not weekly))
            with self.assertRaises(ValidationError):
                validate_schedule_context(target, other, rows["A1"], rows["D9"], expected_type=1)


class ScheduleLimitsTests(unittest.TestCase):
    def test_public_limits_are_attached_to_all_reviewed_programs_and_parameters(self):
        rows = schedule_catalog()
        for programs in CIRCUIT_SCHEDULE_PARAMETERS.values():
            for program in (programs.daily, programs.weekly):
                with self.subTest(name=program.name):
                    limits = program.limits
                    self.assertIsInstance(limits, ScheduleLimits)
                    self.assertIs(limits, get_schedule_limits(program.name))
                    self.assertIs(limits, parameter_from_json(rows[program.name]).schedule_limits)
                    expected = DAILY_SCHEDULE_LIMITS if program.kind == "daily" else WEEKLY_SCHEDULE_LIMITS
                    self.assertIs(limits, expected)
        self.assertIs(DailySchedule().limits, DAILY_SCHEDULE_LIMITS)
        self.assertIs(WeeklySchedule(()).limits, WEEKLY_SCHEDULE_LIMITS)

    def test_queryable_shape_time_and_replacement_rules(self):
        daily, weekly = DAILY_SCHEDULE_LIMITS, WEEKLY_SCHEDULE_LIMITS
        self.assertIsNone(daily.min_day_groups)
        self.assertIsNone(daily.max_day_groups)
        self.assertFalse(daily.supports_weekdays)
        self.assertEqual(daily.daily_wire_slots, 2)
        self.assertTrue(weekly.supports_weekdays)
        self.assertEqual((weekly.min_day_groups, weekly.max_day_groups), (1, 2))
        self.assertIsNone(weekly.daily_wire_slots)
        self.assertTrue(weekly.require_all_weekdays)
        self.assertFalse(daily.require_all_weekdays)
        for limits in (daily, weekly):
            self.assertEqual((limits.min_windows_per_day, limits.max_windows_per_day), (0, 2))
            self.assertEqual(limits.time_resolution_minutes, 1)
            self.assertEqual((limits.min_start_time, limits.max_start_time), ("00:00:00", "23:59:00"))
            self.assertEqual((limits.min_end_time, limits.max_end_time), ("00:01:00", "24:00:00"))
            self.assertTrue(limits.allow_end_of_day)
            self.assertFalse(limits.allow_overnight)
            self.assertFalse(limits.allow_overlaps)
            self.assertTrue(limits.require_unique_weekdays)
            self.assertTrue(limits.full_replacement)
            self.assertTrue(limits.expected_required)
            self.assertEqual(limits.weekdays, tuple(Weekday))
            self.assertEqual(limits.clock_basis, "controller_local")
            self.assertEqual(limits.scope, "reviewed_sdk_write_policy")

    def test_limits_are_immutable_and_not_injected_into_wire_metadata(self):
        limits = get_schedule_limits("3")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            limits.max_windows_per_day = 99
        with self.assertRaises(TypeError):
            limits.weekdays[0] = 7
        row = dict(schedule_catalog()["3"], schedule_limits={"max_windows_per_day": 99})
        parameter = parameter_from_json(row)
        self.assertIs(parameter.schedule_limits, DAILY_SCHEDULE_LIMITS)
        self.assertEqual(parameter.to_dict(), row)
        plain = parameter_from_json(schedule_catalog()["3"])
        self.assertNotIn("schedule_limits", plain.to_dict())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parameter.schedule_limits = None

    def test_no_limits_are_guessed_for_other_schedule_or_non_schedule_parameters(self):
        for name in ("1", "1a", "HP1", "D5", "D9", "4", "A1z", "unknown"):
            with self.subTest(name=name):
                self.assertIsNone(get_schedule_limits(name))

    def test_validator_boundaries_match_advertised_limits(self):
        limits = DAILY_SCHEDULE_LIMITS
        for start, end in ((limits.min_start_time, limits.min_end_time),
                           (limits.max_start_time, limits.max_end_time)):
            value = normalize_schedule("3", [TimeRange(start, end)])
            self.assertEqual(value.time_ranges, (TimeRange(start, end),))
        windows = [TimeRange(f"{hour:02}:00", f"{hour + 1:02}:00")
                   for hour in range(limits.max_windows_per_day + 1)]
        with self.assertRaises(ValidationError):
            normalize_schedule("3", windows)
        groups = [WeeklyTimeRanges((1, 2), ()), WeeklyTimeRanges((3, 4), ()),
                  WeeklyTimeRanges((5, 6, 7), ())]
        self.assertGreater(len(groups), WEEKLY_SCHEDULE_LIMITS.max_day_groups)
        with self.assertRaises(ValidationError):
            normalize_schedule("3a", groups)

    def test_serializer_and_live_slot_validation_use_the_same_limits(self):
        # A private test patch proves single-source consumption, not a supported
        # caller configuration mechanism. Public operations accept no override.
        tighter = dataclasses.replace(
            DAILY_SCHEDULE_LIMITS, max_windows_per_day=1, daily_wire_slots=1,
            time_resolution_minutes=15, min_start_time="06:00:00", max_start_time="19:45:00",
            min_end_time="06:15:00", max_end_time="20:00:00",
        )
        with patch("hvclient.schedules.DAILY_SCHEDULE_LIMITS", tighter):
            self.assertIs(get_schedule_limits("3"), tighter)
            self.assertEqual(len(DailySchedule((TimeRange("06:00", "07:00"),)).to_json()), 1)
            for value in ([TimeRange("06:01", "07:00")], [TimeRange("05:45", "07:00")],
                          [TimeRange("06:00", "20:15")],
                          [TimeRange("06:00", "07:00"), TimeRange("08:00", "09:00")]):
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    normalize_schedule("3", value)
            with self.assertRaises(ValidationError):
                validate_schedule_parameter(parameter_from_json(schedule_catalog()["3"]), active=True)

    def test_weekly_normalizer_and_context_group_checks_use_same_limits(self):
        tighter = dataclasses.replace(WEEKLY_SCHEDULE_LIMITS, max_day_groups=1)
        rows = schedule_catalog(weekly=True)
        with patch("hvclient.schedules.WEEKLY_SCHEDULE_LIMITS", tighter):
            self.assertIs(CIRCUIT_SCHEDULE_PARAMETERS["1"].weekly.limits, tighter)
            with self.assertRaises(ValidationError):
                normalize_schedule("3a", rows["3a"]["value"])
            with self.assertRaises(ValidationError):
                validate_schedule_parameter(parameter_from_json(rows["3a"]), active=True)
            single = WeeklySchedule((WeeklyTimeRanges(tuple(Weekday), ()),))
            self.assertEqual(len(single.to_json()), 1)
