from datetime import date, datetime
import unittest

from project.reporting_week import (
    completed_reporting_week,
    previous_reporting_week,
    reporting_week_for,
    resolve_closed_reporting_week,
)
from project.telemetry_contract import IST


class ReportingWeekTests(unittest.TestCase):
    def test_calendar_week_is_monday_through_sunday(self):
        monday = reporting_week_for(date(2026, 8, 24))
        sunday = reporting_week_for(date(2026, 8, 30))

        self.assertEqual(monday, sunday)
        self.assertEqual(monday.start, date(2026, 8, 24))
        self.assertEqual(monday.end, date(2026, 8, 30))
        self.assertEqual(monday.telemetry_start.isoformat(), "2026-08-23T20:00:00+05:30")
        self.assertEqual(monday.telemetry_end.isoformat(), "2026-08-30T20:00:00+05:30")

    def test_latest_completed_week_rolls_at_sunday_20_ist(self):
        before = completed_reporting_week(datetime(2026, 8, 30, 19, 59, tzinfo=IST))
        after = completed_reporting_week(datetime(2026, 8, 30, 20, 0, tzinfo=IST))

        self.assertEqual((before.start, before.end), (date(2026, 8, 17), date(2026, 8, 23)))
        self.assertEqual((after.start, after.end), (date(2026, 8, 24), date(2026, 8, 30)))

    def test_previous_week_is_stable_from_any_day(self):
        previous = previous_reporting_week(date(2026, 8, 27))
        self.assertEqual((previous.start, previous.end), (date(2026, 8, 17), date(2026, 8, 23)))

    def test_closed_week_defaults_to_previous_week_on_monday(self):
        resolved = resolve_closed_reporting_week(
            as_of=datetime(2026, 8, 31, 1, 15, tzinfo=IST)
        )
        self.assertEqual(
            (resolved.start, resolved.end),
            (date(2026, 8, 24), date(2026, 8, 30)),
        )

    def test_explicit_audit_target_must_be_a_closed_sunday(self):
        before_close = datetime(2026, 8, 30, 19, 59, tzinfo=IST)
        at_close = datetime(2026, 8, 30, 20, 0, tzinfo=IST)

        with self.assertRaisesRegex(ValueError, "Sunday"):
            resolve_closed_reporting_week(date(2026, 8, 29), as_of=at_close)
        with self.assertRaisesRegex(ValueError, "open or lies in the future"):
            resolve_closed_reporting_week(date(2026, 8, 30), as_of=before_close)
        resolved = resolve_closed_reporting_week(date(2026, 8, 30), as_of=at_close)
        self.assertEqual(resolved.end, date(2026, 8, 30))


if __name__ == "__main__":
    unittest.main()
