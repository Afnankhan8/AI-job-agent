"""
Offline pipeline test — mocks Adzuna's HTTP response so we can verify
normalize -> freshness -> dedup -> store works correctly without
needing live network access. Run this before wiring in real credentials.
"""

import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from config.settings import Settings
from database.models import init_db, get_connection
from database.repository import JobRepository
from job_sources.adzuna import AdzunaConnector
from jobs.normalizer import normalize_job
from jobs.freshness import classify_freshness
from jobs.deduplicator import check_duplicate

TEST_DB = "test_jobs.db"

MOCK_RESPONSE = {
    "count": 2,
    "results": [
        {
            "id": "1001",
            "title": "AI Engineer",
            "company": {"display_name": "Leap Interactive LLC"},
            "location": {"display_name": "Dubai, UAE"},
            "description": "Build AI systems.",
            "redirect_url": "https://example.com/job/1001",
            "salary_min": 8000,
            "salary_max": 12000,
            "salary_currency": "AED",
            "contract_type": "permanent",
            "created": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
        },
        {
            "id": "1002",
            "title": "Artificial Intelligence Engineer",  # should normalize to same title as above
            "company": {"display_name": "Leap Interactive"},  # should normalize to same company
            "location": {"display_name": "Dubai, UAE"},
            "description": "Duplicate-ish posting from another feed.",
            "redirect_url": "https://example.com/job/1002",
            "salary_min": 8000,
            "salary_max": 12000,
            "salary_currency": "AED",
            "contract_type": "permanent",
            "created": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat().replace("+00:00", "Z"),
        },
    ],
}


def test_full_pipeline():
    import os
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    init_db(TEST_DB)
    conn = get_connection(TEST_DB)
    repo = JobRepository(conn)

    connector = AdzunaConnector(app_id="test", app_key="test")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_RESPONSE

    with patch("requests.get", return_value=mock_resp):
        raw_jobs = connector.fetch_jobs(what="AI Engineer", where="Dubai", page=1)

    assert len(raw_jobs) == 2, f"Expected 2 raw jobs, got {len(raw_jobs)}"
    print("✅ Connector parsed mocked response correctly")

    inserted, updated = 0, 0
    for raw in raw_jobs:
        normalized = normalize_job(raw)
        decision = check_duplicate(normalized, repo)
        freshness = classify_freshness(
            created_at_source=normalized.created_at_source,
            first_seen_at=datetime.now(timezone.utc),
        )
        if decision.is_duplicate:
            repo.mark_seen_again(decision.existing_job_id, normalized.source_name, freshness)
            updated += 1
        else:
            repo.insert_job(normalized, freshness)
            inserted += 1

    # These two postings normalize to the SAME title + company + location,
    # so the deduplicator should treat the second one as a duplicate of the first.
    assert inserted == 1, f"Expected 1 new job inserted, got {inserted}"
    assert updated == 1, f"Expected 1 duplicate update, got {updated}"
    print("✅ Deduplication correctly merged the two similar postings")

    jobs = repo.list_active_jobs()
    assert len(jobs) == 1, f"Expected 1 stored job, got {len(jobs)}"
    job = jobs[0]
    assert job.title == "AI Engineer"
    assert job.freshness_label == "TODAY"  # first job was 3h old, same calendar day -> TODAY
    assert set(job.found_on_sources) == {"adzuna"}
    print(f"✅ Stored job freshness label: {job.freshness_label}")
    print(f"✅ Job found on sources: {job.found_on_sources}")

    conn.close()
    os.remove(TEST_DB)
    print("\n🎯 Full pipeline test passed: fetch -> normalize -> freshness -> dedup -> store")


if __name__ == "__main__":
    test_full_pipeline()
