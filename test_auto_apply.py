"""
Tests for the AI Job Agent auto-apply system.
Run with:  python test_auto_apply.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from database.models import init_db, get_connection
from database.repository import JobRepository
from config.profile_loader import CandidateProfile
from applier.url_resolver import resolve_url


class TestProfileLoader(unittest.TestCase):
    def test_load_valid_profile(self):
        data = {
            "personal": {
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "phone": "+971501234567",
                "location": "Dubai, UAE",
                "linkedin_url": "https://linkedin.com/in/jane",
                "github_url": "",
                "portfolio_url": "",
                "resume_path": "resume.pdf",
            },
            "work_preferences": {
                "desired_title": "AI Engineer",
                "expected_salary": "30000 AED",
                "notice_period": "1 month",
                "work_authorization": True,
                "requires_sponsorship": False,
                "years_of_experience": 5,
            },
            "cover_letter_template": "Dear Hiring Team, I am interested.",
            "screening_answers": {},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            profile = CandidateProfile.load_from_file(path)
            self.assertEqual(profile.personal.first_name, "Jane")
            self.assertEqual(profile.personal.email, "jane@example.com")
            self.assertTrue(profile.work_preferences.work_authorization)
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            CandidateProfile.load_from_file("/nonexistent/path/profile.json")


class TestRepository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        init_db(self.tmp.name)
        self.conn = get_connection(self.tmp.name)
        self.repo = JobRepository(self.conn)
        # Insert a test job directly
        self.conn.execute(
            """
            INSERT INTO jobs (
                dedup_key, source_name, source_job_id, title, title_normalized,
                first_seen_at, last_seen_at, status, found_on_sources, url
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            ("key1", "jooble", "j1", "AI Engineer", "ai engineer",
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
             "ACTIVE", '["jooble"]', "https://jooble.org/jdp/123"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_get_unapplied_jobs(self):
        jobs = self.repo.get_unapplied_jobs(limit=10)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "AI Engineer")

    def test_create_and_update_application(self):
        jobs = self.repo.get_unapplied_jobs(limit=10)
        job_id = jobs[0].id

        app_id = self.repo.create_application(job_id, status="PENDING")
        self.assertGreater(app_id, 0)

        self.repo.update_application(
            app_id=app_id,
            status="APPLIED",
            redirect_url="https://company.com/apply",
            screenshot_path="screenshots/job_1.png",
            logs=["Filled name", "Filled email", "Submitted"],
        )

        summary = self.repo.get_application_summary()
        self.assertEqual(summary.get("APPLIED"), 1)

        # After applying, job should no longer appear as unapplied
        unapplied = self.repo.get_unapplied_jobs(limit=10)
        self.assertEqual(len(unapplied), 0)

    def test_failed_application_can_be_retried(self):
        jobs = self.repo.get_unapplied_jobs(limit=10)
        job_id = jobs[0].id

        app_id = self.repo.create_application(job_id, status="PENDING")
        self.repo.update_application(app_id, status="REQUIRES_MANUAL")

        retryable = self.repo.get_unapplied_jobs(limit=10)
        self.assertEqual(len(retryable), 1)
        self.assertEqual(retryable[0].id, job_id)

    def test_detected_already_applied_can_be_rechecked(self):
        jobs = self.repo.get_unapplied_jobs(limit=10)
        job_id = jobs[0].id

        app_id = self.repo.create_application(job_id, status="APPLIED")
        self.repo.update_application(
            app_id,
            status="APPLIED",
            error_reason="Already applied (detected on page).",
        )

        retryable = self.repo.get_unapplied_jobs(limit=10)
        self.assertEqual(len(retryable), 1)
        self.assertEqual(retryable[0].id, job_id)

    def test_clear_jobs_deletes_only_unanswered_applications_older_than_90_days(self):
        old_job_id = self.repo.get_unapplied_jobs(limit=10)[0].id
        old_app_id = self.repo.create_application(old_job_id)
        self.repo.update_application(old_app_id, status="APPLIED")
        old_applied_at = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        self.conn.execute(
            "UPDATE applications SET applied_at = ? WHERE id = ?",
            (old_applied_at, old_app_id),
        )

        self.conn.execute(
            """
            INSERT INTO jobs (
                dedup_key, source_name, source_job_id, title, title_normalized,
                first_seen_at, last_seen_at, status, found_on_sources, url
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            ("key2", "jooble", "j2", "ML Engineer", "ml engineer",
             old_applied_at, old_applied_at, "ACTIVE", '["jooble"]',
             "https://jooble.org/jdp/456"),
        )
        recent_job_id = self.conn.execute(
            "SELECT id FROM jobs WHERE dedup_key = 'key2'"
        ).fetchone()[0]
        recent_app_id = self.repo.create_application(recent_job_id)
        self.repo.update_application(recent_app_id, status="APPLIED")
        self.conn.commit()

        result = self.repo.clear_jobs(clear_applied=True, clear_old=False)

        self.assertEqual(result["deleted_jobs"], 1)
        self.assertIsNone(
            self.conn.execute(
                "SELECT id FROM jobs WHERE id = ?", (old_job_id,)
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT id FROM jobs WHERE id = ?", (recent_job_id,)
            ).fetchone()
        )

    def test_clear_jobs_preserves_replied_applications(self):
        job_id = self.repo.get_unapplied_jobs(limit=10)[0].id
        app_id = self.repo.create_application(job_id)
        self.repo.update_application(app_id, status="APPLIED")
        old_applied_at = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        self.conn.execute(
            """
            UPDATE applications
            SET applied_at = ?, response_status = 'RESPONSE_RECEIVED',
                response_received_at = ?
            WHERE id = ?
            """,
            (old_applied_at, old_applied_at, app_id),
        )
        self.conn.commit()

        result = self.repo.clear_jobs(clear_applied=True, clear_old=False)

        self.assertEqual(result["deleted_jobs"], 0)
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT id FROM applications WHERE id = ?", (app_id,)
            ).fetchone()
        )


class TestUrlResolver(unittest.TestCase):
    def test_resolves_real_url(self):
        url, history = resolve_url("https://httpbin.org/get")
        self.assertIn("httpbin.org", url)
        self.assertGreater(len(history), 0)  # at least the original URL is in history

    def test_empty_url(self):
        url, history = resolve_url("")
        self.assertEqual(url, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
