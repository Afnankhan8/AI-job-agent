"""
Repository layer — the ONLY place with SQL.

Everything else talks to these methods, never to the database directly.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List

from jobs.normalizer import NormalizedJob
from jobs.freshness import FreshnessResult


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class JobRecord:
    id: int
    dedup_key: str
    source_name: str
    title: str
    company: Optional[str]
    location: Optional[str]
    description: Optional[str]
    url: Optional[str]
    freshness_label: Optional[str]
    status: str
    first_seen_at: str
    last_seen_at: str
    found_on_sources: List[str]
    ai_match_score: Optional[int] = None
    ai_recommendation: Optional[str] = None
    ai_cover_letter: Optional[str] = None


@dataclass
class ApplicationRecord:
    id: int
    job_id: int
    status: str
    redirect_url: Optional[str]
    applied_at: Optional[str]
    error_reason: Optional[str]
    screenshot_path: Optional[str]
    logs: List[str]


# ── Repository ────────────────────────────────────────────────────────────────

class JobRepository:
    _FRESHNESS_RANK = ["TODAY", "NEW", "RECENT", "OLD", "UNKNOWN"]

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def find_by_dedup_key(self, dedup_key: str) -> Optional[JobRecord]:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def insert_job(self, job: NormalizedJob, freshness: FreshnessResult) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            """
            INSERT INTO jobs (
                dedup_key, source_name, source_job_id,
                title, title_normalized,
                company, company_normalized,
                location, description, url,
                salary_min, salary_max, currency, contract_type,
                created_at_source, first_seen_at, last_seen_at,
                freshness_label, status, found_on_sources
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?)
            """,
            (
                job.dedup_key, job.source_name, job.source_job_id,
                job.title, job.title_normalized,
                job.company, job.company_normalized,
                job.location, job.description, job.url,
                job.salary_min, job.salary_max, job.currency, job.contract_type,
                job.created_at_source.isoformat() if job.created_at_source else None,
                now, now,
                freshness.label.value,
                json.dumps([job.source_name]),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def mark_seen_again(self, job_id: int, source_name: str, freshness: FreshnessResult) -> None:
        row = self.conn.execute(
            "SELECT found_on_sources, freshness_label FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        sources = json.loads(row["found_on_sources"]) if row else []
        if source_name not in sources:
            sources.append(source_name)

        best = self._pick_fresher_label(
            row["freshness_label"] if row else None,
            freshness.label.value,
        )
        self.conn.execute(
            """
            UPDATE jobs
            SET last_seen_at = ?, freshness_label = ?, found_on_sources = ?, status = 'ACTIVE'
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), best, json.dumps(sources), job_id),
        )
        self.conn.commit()

    def list_active_jobs(self, limit: int = 100, unapplied_only: bool = False,
                         exclude_detected_applied: bool = True) -> List[JobRecord]:
        """
        List active jobs.
        - unapplied_only: exclude jobs that have ANY application record
        - exclude_detected_applied: also exclude jobs flagged 'Already applied (detected on page)'
        """
        if unapplied_only:
            rows = self.conn.execute(
                """
                SELECT j.* FROM jobs j
                LEFT JOIN applications a ON a.job_id = j.id
                WHERE j.status = 'ACTIVE' AND a.id IS NULL
                ORDER BY
                    CASE j.freshness_label
                        WHEN 'TODAY'  THEN 1
                        WHEN 'NEW'    THEN 2
                        WHEN 'RECENT' THEN 3
                        ELSE 4
                    END,
                    j.last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        elif exclude_detected_applied:
            # Exclude jobs where application says 'Already applied (detected on page)'
            rows = self.conn.execute(
                """
                SELECT j.* FROM jobs j
                WHERE j.status = 'ACTIVE'
                AND j.id NOT IN (
                    SELECT DISTINCT job_id FROM applications
                    WHERE error_reason LIKE '%Already applied%'
                )
                ORDER BY
                    CASE j.freshness_label
                        WHEN 'TODAY'  THEN 1
                        WHEN 'NEW'    THEN 2
                        WHEN 'RECENT' THEN 3
                        ELSE 4
                    END,
                    j.last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE status = 'ACTIVE' ORDER BY last_seen_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_unapplied_jobs(self, limit: int = 100, fresh_only: bool = False) -> List[JobRecord]:
        """
        Active jobs without a successful or in-progress application.

        Failed and manual-required attempts remain eligible for retry. If
        fresh_only=True, only TODAY/NEW jobs (posted < 24h) are returned.
        Always prioritises freshest first.
        """
        freshness_filter = "AND j.freshness_label IN ('TODAY', 'NEW')" if fresh_only else ""
        rows = self.conn.execute(
            f"""
            SELECT j.*
            FROM   jobs j
            WHERE  j.status = 'ACTIVE'
            AND NOT EXISTS (
                SELECT 1 FROM applications a
                WHERE a.job_id = j.id
                AND (
                    a.status IN ('PENDING', 'SKIPPED')
                    OR (
                        a.status = 'APPLIED'
                        AND (a.error_reason IS NULL OR a.error_reason NOT LIKE 'Already applied%')
                    )
                )
            )
            {freshness_filter}
            ORDER BY
                CASE j.freshness_label
                    WHEN 'TODAY'  THEN 1
                    WHEN 'NEW'    THEN 2
                    WHEN 'RECENT' THEN 3
                    ELSE 4
                END,
                j.last_seen_at DESC
            LIMIT  ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_already_applied_detected(self, limit: int = 200) -> list:
        """Return applications flagged as 'Already applied (detected on page)' for the separate tracker UI."""
        rows = self.conn.execute(
            """
            SELECT a.*, j.title, j.company, j.url AS job_url, j.location, j.ai_match_score,
                   j.freshness_label, j.last_seen_at AS job_last_seen
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            WHERE a.error_reason LIKE '%Already applied%'
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fresh_stats(self) -> dict:
        """Stats specifically for fresh (TODAY/NEW) unapplied jobs."""
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total_fresh,
                SUM(CASE WHEN j.freshness_label = 'TODAY' THEN 1 ELSE 0 END) AS today_count,
                SUM(CASE WHEN j.freshness_label = 'NEW' THEN 1 ELSE 0 END) AS new_count,
                SUM(CASE WHEN j.ai_match_score >= 80 THEN 1 ELSE 0 END) AS strong_fresh
            FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status = 'ACTIVE'
            AND j.freshness_label IN ('TODAY', 'NEW')
            AND a.id IS NULL
            """
        ).fetchone()
        return dict(row) if row else {}

    def get_application_summary_clean(self) -> dict:
        """Application summary excluding 'Already applied (detected)' records."""
        rows = self.conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM applications
            WHERE (error_reason NOT LIKE '%Already applied%' OR error_reason IS NULL)
            GROUP BY status
            """
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def prune_expired_jobs(self, days: int = 30) -> int:
        """Mark jobs that haven't been seen in `days` as EXPIRED."""
        cursor = self.conn.execute(
            """
            UPDATE jobs
            SET status = 'EXPIRED'
            WHERE status = 'ACTIVE'
            AND last_seen_at < datetime('now', '-' || ? || ' days')
            """,
            (days,)
        )
        self.conn.commit()
        return cursor.rowcount

    def clear_jobs(
        self,
        clear_applied: bool = True,
        clear_old: bool = True,
        unanswered_days: int = 90,
    ) -> dict:
        """Delete untracked old jobs and unanswered real applications after 90 days."""
        conditions = []
        params = []
        if clear_old:
            conditions.append(
                "(a.id IS NULL AND (j.freshness_label = 'OLD' OR j.status = 'EXPIRED'))"
            )
        if clear_applied:
            conditions.append(
                "(a.status = 'APPLIED' "
                "AND a.applied_at IS NOT NULL "
                "AND a.response_received_at IS NULL "
                "AND a.applied_at < datetime('now', '-' || ? || ' days'))"
            )
            params.append(unanswered_days)
            conditions.append("a.error_reason LIKE '%Already applied%'")

        if not conditions:
            return {"deleted_jobs": 0, "deleted_apps": 0}

        cond_str = " OR ".join(conditions)
        select_to_delete = f"""
            SELECT DISTINCT j.id FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.id
            WHERE {cond_str}
        """
        rows = self.conn.execute(select_to_delete, params).fetchall()
        job_ids = [r[0] for r in rows]

        if not job_ids:
            return {"deleted_jobs": 0, "deleted_apps": 0}

        deleted_jobs = 0
        deleted_apps = 0
        
        chunk_size = 900
        for i in range(0, len(job_ids), chunk_size):
            chunk = job_ids[i:i+chunk_size]
            ids_str = ",".join("?" for _ in chunk)
            
            apps_cur = self.conn.execute(f"DELETE FROM applications WHERE job_id IN ({ids_str})", chunk)
            jobs_cur = self.conn.execute(f"DELETE FROM jobs WHERE id IN ({ids_str})", chunk)
            
            deleted_apps += apps_cur.rowcount
            deleted_jobs += jobs_cur.rowcount
            
        self.conn.commit()
        return {"deleted_jobs": deleted_jobs, "deleted_apps": deleted_apps}

    # ── AI Analysis ───────────────────────────────────────────────────────────

    def update_ai_analysis(
        self,
        job_id: int,
        score: int,
        recommendation: str,
        cover_letter: Optional[str] = None,
    ) -> None:
        """Store the AI match analysis for a job."""
        self.conn.execute(
            """
            UPDATE jobs
            SET ai_match_score = ?, ai_recommendation = ?, ai_cover_letter = ?
            WHERE id = ?
            """,
            (score, recommendation, cover_letter, job_id),
        )
        self.conn.commit()

    def get_unanalyzed_jobs(self, limit: int = 100) -> List[JobRecord]:
        """Active jobs that haven't been scored by the AI yet."""
        rows = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'ACTIVE'
            AND   ai_match_score IS NULL
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_jobs_missing_description(self, limit: int = 200) -> List[JobRecord]:
        """Active jobs that have no description yet (empty string or NULL)."""
        rows = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'ACTIVE'
            AND   (description IS NULL OR description = '')
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_description(self, job_id: int, description: str) -> None:
        """Store a scraped job description."""
        self.conn.execute(
            "UPDATE jobs SET description = ? WHERE id = ?",
            (description, job_id),
        )
        self.conn.commit()

    def get_recommended_jobs(self, min_score: int = 50, limit: int = 100) -> List[JobRecord]:
        """Active, AI-scored jobs above the threshold that haven't been applied to."""
        rows = self.conn.execute(
            """
            SELECT j.*
            FROM   jobs j
            LEFT   JOIN applications a
                   ON  a.job_id = j.id
                   AND a.status IN ('APPLIED', 'PENDING')
            WHERE  j.status = 'ACTIVE'
            AND    j.ai_match_score >= ?
            AND    a.id IS NULL
            ORDER  BY j.ai_match_score DESC
            LIMIT  ?
            """,
            (min_score, limit),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_ai_analysis_summary(self) -> dict:
        """Return counts of AI recommendations."""
        rows = self.conn.execute(
            """
            SELECT ai_recommendation, COUNT(*) AS n, AVG(ai_match_score) AS avg_score
            FROM jobs
            WHERE ai_match_score IS NOT NULL
            GROUP BY ai_recommendation
            """
        ).fetchall()
        return {
            r["ai_recommendation"]: {"count": r["n"], "avg_score": round(r["avg_score"], 1)}
            for r in rows
        }

    # ── Applications ──────────────────────────────────────────────────────────

    def create_application(self, job_id: int, status: str = "PENDING") -> int:
        cur = self.conn.execute(
            "INSERT INTO applications (job_id, status, logs) VALUES (?, ?, ?)",
            (job_id, status, json.dumps(["Application record created"])),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_application(
        self,
        app_id: int,
        status: str,
        redirect_url: Optional[str] = None,
        screenshot_path: Optional[str] = None,
        error_reason: Optional[str] = None,
        logs: Optional[List[str]] = None,
        ai_match_score: Optional[int] = None,
        ai_cover_letter: Optional[str] = None,
    ) -> None:
        applied_at = datetime.now(timezone.utc).isoformat() if status == "APPLIED" else None
        self.conn.execute(
            """
            UPDATE applications
            SET    status = ?, redirect_url = ?, applied_at = ?,
                   screenshot_path = ?, error_reason = ?, logs = ?,
                   ai_match_score = ?, ai_cover_letter = ?
            WHERE  id = ?
            """,
            (
                status, redirect_url, applied_at,
                screenshot_path, error_reason,
                json.dumps(logs or []),
                ai_match_score, ai_cover_letter,
                app_id,
            ),
        )
        self.conn.commit()

    def get_application_summary(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def record_application_response(
        self,
        app_id: int,
        response_status: str,
        received_at: str,
        subject: str,
        sender: str,
        snippet: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE applications
            SET response_status = ?, response_received_at = ?,
                response_subject = ?, response_sender = ?, response_snippet = ?
            WHERE id = ?
            """,
            (response_status, received_at, subject, sender, snippet, app_id),
        )
        self.conn.commit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @classmethod
    def _pick_fresher_label(cls, existing: Optional[str], new: str) -> str:
        if existing is None:
            return new
        ei = cls._FRESHNESS_RANK.index(existing) if existing in cls._FRESHNESS_RANK else 99
        ni = cls._FRESHNESS_RANK.index(new) if new in cls._FRESHNESS_RANK else 99
        return existing if ei <= ni else new

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            dedup_key=row["dedup_key"],
            source_name=row["source_name"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            description=row["description"] if "description" in row.keys() else None,
            url=row["url"],
            freshness_label=row["freshness_label"],
            status=row["status"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            found_on_sources=json.loads(row["found_on_sources"]),
            ai_match_score=row["ai_match_score"] if "ai_match_score" in row.keys() else None,
            ai_recommendation=row["ai_recommendation"] if "ai_recommendation" in row.keys() else None,
            ai_cover_letter=row["ai_cover_letter"] if "ai_cover_letter" in row.keys() else None,
        )
