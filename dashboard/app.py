"""
dashboard/app.py — AI Job Agent Dashboard (Flask web app).

Launch with: python dashboard/app.py
Opens automatically at http://localhost:5000
"""

import json
import os
import sys
import subprocess
import threading
import webbrowser
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, jsonify, request, redirect, url_for
from config.settings import SETTINGS
from database.models import init_db, get_connection
from database.repository import JobRepository

app = Flask(__name__)
app.secret_key = "ai-job-agent-dashboard"


# ── Template filters ──────────────────────────────────────────────────────────
from markupsafe import Markup, escape

@app.template_filter('nl2br')
def nl2br(value):
    """Convert newlines to <br> tags in Jinja2 templates."""
    return Markup(escape(value).replace('\n', Markup('<br>')))


def get_repo():
    conn = get_connection(SETTINGS.database_path)
    return conn, JobRepository(conn)


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    conn, repo = get_repo()
    try:
        all_jobs   = repo.list_active_jobs(limit=1000)
        today      = sum(1 for j in all_jobs if j.freshness_label in ("TODAY", "NEW"))
        scored     = sum(1 for j in all_jobs if j.ai_match_score is not None)
        strong     = sum(1 for j in all_jobs if (j.ai_match_score or 0) >= 80)
        good       = sum(1 for j in all_jobs if 60 <= (j.ai_match_score or 0) < 80)
        app_summary = repo.get_application_summary()
        applied    = app_summary.get("APPLIED", 0)
        pending    = app_summary.get("PENDING", 0)
        return jsonify({
            "total":   len(all_jobs),
            "today":   today,
            "scored":  scored,
            "strong":  strong,
            "good":    good,
            "applied": applied,
            "pending": pending,
        })
    finally:
        conn.close()


@app.route("/api/apply/<int:job_id>", methods=["POST"])
def api_apply(job_id):
    """Trigger Playwright auto-apply for a single job (AJAX)."""
    dry_run = request.json.get("dry_run", True)
    headful = request.json.get("headful", False)

    conn, repo = get_repo()
    jobs = [j for j in repo.list_active_jobs(limit=1000) if j.id == job_id]
    conn.close()

    if not jobs:
        return jsonify({"error": "Job not found"}), 404

    try:
        from auto_apply import run_auto_apply
        from database.models import get_connection as gc
        from database.repository import JobRepository as JR

        # Run apply in background-compatible way
        c2 = gc(SETTINGS.database_path)
        r2 = JR(c2)
        job = jobs[0]

        # Build a minimal profile
        from config.profile_loader import CandidateProfile
        profile = CandidateProfile.load_from_file(SETTINGS.user_profile_path)

        from playwright.sync_api import sync_playwright
        from applier.browser_engine import ApplicationEngine

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(Path(__file__).parent.parent / "browser_profile"),
                headless=not headful,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            engine = ApplicationEngine(
                profile=profile,
                context=ctx,
                dry_run=dry_run,
                screenshots_dir=SETTINGS.screenshots_dir,
            )
            # Set AI cover letter if available
            engine.ai_cover_letter = job.ai_cover_letter

            app_id = r2.create_application(job.id, status="PENDING")
            outcome = engine.apply(job)
            r2.update_application(
                app_id=app_id,
                status=outcome.status,
                redirect_url=outcome.redirect_url,
                screenshot_path=outcome.screenshot_path,
                error_reason=outcome.error_reason,
                logs=outcome.logs,
                ai_match_score=job.ai_match_score,
                ai_cover_letter=job.ai_cover_letter,
            )
            ctx.close()

        c2.close()
        return jsonify({"status": outcome.status, "error": outcome.error_reason})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/jobs")
def jobs_page():
    conn, repo = get_repo()
    try:
        all_jobs = repo.list_active_jobs(limit=500)

        # Filters from query string
        q         = request.args.get("q", "").lower()
        freshness = request.args.get("freshness", "")
        source    = request.args.get("source", "")
        min_score = request.args.get("min_score", "")
        sort      = request.args.get("sort", "recent")

        filtered = all_jobs

        if q:
            filtered = [j for j in filtered
                        if q in (j.title or "").lower()
                        or q in (j.company or "").lower()]
        if freshness:
            filtered = [j for j in filtered if j.freshness_label == freshness]
        if source:
            filtered = [j for j in filtered if source in j.found_on_sources]
        if min_score:
            try:
                ms = int(min_score)
                filtered = [j for j in filtered if (j.ai_match_score or 0) >= ms]
            except ValueError:
                pass

        if sort == "score":
            filtered.sort(key=lambda j: j.ai_match_score or 0, reverse=True)
        elif sort == "company":
            filtered.sort(key=lambda j: (j.company or "").lower())
        # default: recent (already ordered by last_seen_at DESC from DB)

        return render_template("jobs.html",
                               jobs=filtered,
                               q=q,
                               freshness=freshness,
                               source=source,
                               min_score=min_score,
                               sort=sort,
                               total=len(filtered))
    finally:
        conn.close()


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    conn, repo = get_repo()
    try:
        all_jobs = repo.list_active_jobs(limit=1000)
        job = next((j for j in all_jobs if j.id == job_id), None)
        if not job:
            return "Job not found", 404

        # Fetch applications for this job
        apps = conn.execute(
            "SELECT * FROM applications WHERE job_id = ? ORDER BY id DESC",
            (job_id,)
        ).fetchall()
        applications = [dict(a) for a in apps]

        return render_template("job_detail.html", job=job, applications=applications)
    finally:
        conn.close()


@app.route("/applications")
def applications_page():
    conn, repo = get_repo()
    try:
        rows = conn.execute(
            """
            SELECT a.*, j.title, j.company, j.url AS job_url, j.location
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            ORDER BY a.id DESC
            LIMIT 200
            """
        ).fetchall()
        apps = [dict(r) for r in rows]
        summary = repo.get_application_summary()
        return render_template("applications.html", apps=apps, summary=summary)
    finally:
        conn.close()


@app.route("/run/fetch", methods=["POST"])
def run_fetch():
    """Trigger a fresh job ingestion + description fetch in the background."""
    def _run():
        import subprocess
        subprocess.run([sys.executable, "main.py", "--no-ai"], cwd=str(Path(__file__).parent.parent))
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/run/score", methods=["POST"])
def run_score():
    """Trigger AI scoring in the background."""
    def _run():
        from ai.scorer import score_unanalyzed_jobs
        from config.profile_loader import CandidateProfile
        from ai.ollama_client import OllamaClient
        conn2, repo2 = get_repo()
        try:
            profile = CandidateProfile.load_from_file(SETTINGS.user_profile_path)
            client = OllamaClient(base_url=SETTINGS.ollama_base_url, model=SETTINGS.ollama_model)
            score_unanalyzed_jobs(repo2, profile, client, limit=200, verbose=False)
        except Exception:
            pass
        finally:
            conn2.close()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "started"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_db(SETTINGS.database_path)
    # Open browser after a short delay
    def _open():
        import time
        time.sleep(1.2)
        webbrowser.open("http://localhost:5000")
    threading.Thread(target=_open, daemon=True).start()
    print("\n  AI Job Agent Dashboard")
    print("  -> http://localhost:5000\n")
    app.run(debug=False, port=5000)
