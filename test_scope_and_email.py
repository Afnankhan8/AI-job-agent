import unittest
from types import SimpleNamespace

from email_tracker import _response_status
from jobs.scope import check_job_scope


def make_job(title, location):
    return SimpleNamespace(title=title, location=location)


class TestScopeAndEmail(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(
            work_preferences=SimpleNamespace(desired_title="AI Engineer")
        )

    def test_scope_accepts_matching_role_and_location(self):
        result = check_job_scope(make_job("AI Engineer", "Dubai, UAE"), self.profile, "UAE")
        self.assertTrue(result.allowed)

    def test_scope_rejects_wrong_location(self):
        result = check_job_scope(make_job("AI Engineer", "London, UK"), self.profile, "UAE")
        self.assertFalse(result.allowed)

    def test_scope_rejects_wrong_role(self):
        result = check_job_scope(make_job("Marketing Manager", "Dubai, UAE"), self.profile, "UAE")
        self.assertFalse(result.allowed)

    def test_linkedin_confirmation_is_distinct(self):
        self.assertEqual(
            _response_status("Thanks for applying to AI Engineer", "Your application was submitted."),
            "APPLICATION_CONFIRMED",
        )

    def test_linkedin_interview_and_rejection_statuses(self):
        self.assertEqual(_response_status("Next steps", "Interview invitation"), "INTERVIEW_OR_NEXT_STEP")
        self.assertEqual(_response_status("Update", "Unfortunately, not selected"), "REJECTED")


if __name__ == "__main__":
    unittest.main(verbosity=2)