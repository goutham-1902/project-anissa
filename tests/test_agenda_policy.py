from datetime import date
import unittest

from logic.mail_identity import check_mail_identity
from logic.application_buckets import build_application_buckets


def address(local="candidate"):
    # Synthetic test identities only; never copy an installation's address.
    return local + "@" + "example.invalid"


class AgendaPolicyTests(unittest.TestCase):
    def test_identity_matches_only_canonical_address(self):
        expected = address()
        for profile, ok in ((None, False), ({}, False),
                            ({"email": expected}, True),
                            ({"emailAddress": expected.upper()}, True),
                            ({"email": address("other")}, False),
                            ({"email": address("candidate+alias")}, False),
                            ({"email": expected, "emailAddress": address("other")}, False)):
            with self.subTest(profile=profile):
                result = check_mail_identity(expected, profile)
                self.assertEqual(result["ok"], ok)
                self.assertEqual(result["blocked"], not ok)

    def test_portal_requires_separate_observed_identity(self):
        expected = address()
        for email, ok in ((None, False), (address("other"), False), (expected, True)):
            result = check_mail_identity(expected, {"email": expected}, operation="portal", portal_email=email)
            self.assertEqual(result["ok"], ok)

    def test_unclassified_applications_remain_distinct(self):
        apps = [{"Application ID": value, "Institution": "Example", "Programme / Position": value,
                 "Status": "Preparing", "Deadline": "2027-01-20"} for value in ("a", "b")]
        result = build_application_buckets(apps, [], {}, as_of=date(2026, 11, 5))
        self.assertEqual(len(result["buckets"]), 2)

    def test_malformed_bucket_metadata_fails_closed(self):
        with self.assertRaises((ValueError, TypeError)):
            build_application_buckets([], [], [], as_of=date(2026, 11, 5))


if __name__ == "__main__":
    unittest.main()
