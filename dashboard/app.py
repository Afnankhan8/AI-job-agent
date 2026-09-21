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
from applier.browser_utils import launch_safe_context
from email_tracker import sync_linkedin_emails

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
        # Only count truly unapplied fresh jobs (exclude detected-applied)
        all_jobs    = repo.list_active_jobs(limit=1000, unapplied_only=True)
        today       = sum(1 for j in all_jobs if j.freshness_label in ("TODAY", "NEW"))
        scored      = sum(1 for j in all_jobs if j.ai_match_score is not None)
        strong      = sum(1 for j in all_jobs if (j.ai_match_score or 0) >= 80)
        good        = sum(1 for j in all_jobs if 60 <= (j.ai_match_score or 0) < 80)
        # Use clean summary — exclude "already applied detected" records
        app_summary = repo.get_application_summary_clean()
        applied     = app_summary.get("APPLIED", 0)
        pending     = app_summary.get("PENDING", 0)
        manual      = app_summary.get("REQUIRES_MANUAL", 0)
        # Count detected-already-applied separately
        already_det = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE error_reason LIKE '%Already applied%'"
        ).fetchone()[0]
        return jsonify({
            "total":        len(all_jobs),
            "today":        today,
            "scored":       scored,
            "strong":       strong,
            "good":         good,
            "applied":      applied,
            "pending":      pending,
            "manual":       manual,
            "already_det":  already_det,
        })
    finally:
        conn.close()


@app.route("/api/feed")
def api_feed():
    """Return latest fresh unapplied jobs — freshest (TODAY/NEW) first."""
    conn, repo = get_repo()
    try:
        # Always show TODAY+NEW at top, then RECENT
        jobs = repo.list_active_jobs(limit=60, unapplied_only=True)
        result = []
        for j in jobs:
            result.append({
                "id":             j.id,
                "title":          j.title or "Untitled",
                "company":        j.company or "Unknown",
                "location":       j.location or "Remote",
                "url":            j.url or "#",
                "freshness":      j.freshness_label or "OLD",
                "score":          j.ai_match_score,
                "recommendation": j.ai_recommendation,
                "sources":        j.found_on_sources,
                "excerpt":        (j.description or "")[:140],
                "last_seen":      j.last_seen_at,
            })
        return jsonify({
            "jobs":      result,
            "count":     len(result),
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        })
    finally:
        conn.close()


@app.route("/api/fresh-stats")
def api_fresh_stats():
    """Return stats for fresh (TODAY + NEW) unapplied jobs only."""
    conn, repo = get_repo()
    try:
        return jsonify(repo.get_fresh_stats())
    finally:
        conn.close()


@app.route("/api/already-applied")
def api_already_applied():
    """Return jobs that were detected as already-applied by the adapter."""
    conn, repo = get_repo()
    try:
        rows = repo.get_already_applied_detected(limit=200)
        return jsonify({"items": rows, "count": len(rows)})
    finally:
        conn.close()


@app.route("/api/trending")
def api_trending():
    """Return trending companies, sources, freshness breakdown, score distribution."""
    from collections import Counter
    conn, repo = get_repo()
    try:
        jobs = repo.list_active_jobs(limit=1000)

        companies = Counter(j.company for j in jobs if j.company)
        sources   = Counter()
        for j in jobs:
            for s in (j.found_on_sources or []):
                sources[s] += 1

        fresh   = Counter(j.freshness_label or "OLD" for j in jobs)
        strong  = sum(1 for j in jobs if (j.ai_match_score or 0) >= 80)
        good    = sum(1 for j in jobs if 60 <= (j.ai_match_score or 0) < 80)
        weak    = sum(1 for j in jobs if 1  <= (j.ai_match_score or 0) < 60)

        hot_today = [
            {"id": j.id, "title": j.title, "company": j.company,
             "score": j.ai_match_score, "location": j.location}
            for j in jobs if j.freshness_label in ("TODAY", "NEW")
        ][:20]

        top_scored = sorted(
            [j for j in jobs if j.ai_match_score is not None],
            key=lambda j: j.ai_match_score or 0, reverse=True
        )[:10]
        top_scored_data = [
            {"id": j.id, "title": j.title, "company": j.company,
             "score": j.ai_match_score, "recommendation": j.ai_recommendation}
            for j in top_scored
        ]

        return jsonify({
            "top_companies":  companies.most_common(8),
            "sources":        dict(sources),
            "freshness":      dict(fresh),
            "score_dist":     {"strong": strong, "good": good, "weak": weak},
            "hot_today":      hot_today,
            "top_scored":     top_scored_data,
        })
    finally:
        conn.close()



@app.route("/api/apply/<int:job_id>", methods=["POST"])
def api_apply(job_id):
    """Trigger V2 adapter auto-apply for a single job (AJAX)."""
    dry_run = request.json.get("dry_run", True)
    headful  = request.json.get("headful", False)

    conn, repo = get_repo()
    all_jobs = repo.list_active_jobs(limit=1000)
    jobs = [j for j in all_jobs if j.id == job_id]
    conn.close()

    if not jobs:
        return jsonify({"error": "Job not found"}), 404

    try:
        from config.profile_loader import CandidateProfile
        from applier.adapters import detect_platform, get_adapter
        from applier.url_resolver import resolve_url
        from ai import get_ai_client
        from playwright.sync_api import sync_playwright
        from database.models import get_connection as gc
        from database.repository import JobRepository as JR

        profile = CandidateProfile.load_from_file(SETTINGS.user_profile_path)
        job = jobs[0]

        # Never submit a job that already has a confirmed application record,
        # even if the request was made directly instead of from the jobs page.
        c2 = gc(SETTINGS.database_path)
        r2 = JR(c2)
        already_applied = c2.execute(
            "SELECT 1 FROM applications WHERE job_id = ? AND status = 'APPLIED' LIMIT 1",
            (job.id,),
        ).fetchone()
        if already_applied:
            c2.close()
            return jsonify({
                "status": "SKIPPED",
                "error": "This job is already marked as applied; no duplicate submission was attempted.",
            })

        # Build AI client (optional — falls back gracefully)
        try:
            ai_client = get_ai_client()
            if not ai_client.health_check():
                ai_client = None
        except Exception:
            ai_client = None

        # AI cover letter
        ai_cover_letter = job.ai_cover_letter or profile.cover_letter_template

        app_id = r2.create_application(job.id, status="PENDING")

        ctx = None
        try:
            with sync_playwright() as p:
                ctx, _ = launch_safe_context(
                    p,
                    user_data_dir=Path(__file__).parent.parent / "browser_profile",
                    headless=not headful,
                )

                resolved_url, _ = resolve_url(job.url)
                platform = detect_platform(resolved_url)
                adapter  = get_adapter(platform)

                outcome = adapter.apply(
                    job=job,
                    profile=profile,
                    context=ctx,
                    dry_run=dry_run,
                    ai_cover_letter=ai_cover_letter,
                    ai_answers={},
                    screenshots_dir=SETTINGS.screenshots_dir,
                    ai_client=ai_client,
                )
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception as pw_err:
            err_msg = str(pw_err)
            if "SingletonLock" in err_msg or "user data directory is already in use" in err_msg.lower():
                err_msg = "Browser profile is locked by another running process. Please wait a moment and try again."
            r2.update_application(
                app_id=app_id,
                status="FAILED",
                redirect_url=job.url,
                screenshot_path=None,
                error_reason=err_msg,
                logs=[f"Playwright launch error: {pw_err}"],
                ai_match_score=job.ai_match_score,
                ai_cover_letter=ai_cover_letter,
            )
            c2.close()
            return jsonify({"status": "FAILED", "error": err_msg})

        r2.update_application(
            app_id=app_id,
            status=outcome.status,
            redirect_url=outcome.redirect_url,
            screenshot_path=outcome.screenshot_path,
            error_reason=outcome.error_reason,
            logs=outcome.logs,
            ai_match_score=job.ai_match_score,
            ai_cover_letter=ai_cover_letter,
        )
        c2.close()
        return jsonify({
            "status": outcome.status,
            "error": outcome.error_reason,
            "application_url": outcome.redirect_url,
        })

    except Exception as e:
        return jsonify({"status": "FAILED", "error": str(e)}), 500


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """Clear old and already applied jobs from the database."""
    conn, repo = get_repo()
    try:
        result = repo.clear_jobs(clear_applied=True, clear_old=True)
        return jsonify({
            "status": "success", 
            "message": f"Cleared {result['deleted_jobs']} jobs and {result['deleted_apps']} apps."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()



# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/jobs")
def jobs_page():
    conn, repo = get_repo()
    try:
        # Default: show unapplied only, freshest first; exclude detected-applied
        all_jobs = repo.list_active_jobs(limit=500, unapplied_only=True)

        # Filters from query string
        q          = request.args.get("q", "").lower()
        freshness  = request.args.get("freshness", "")
        source     = request.args.get("source", "")
        min_score  = request.args.get("min_score", "")
        sort       = request.args.get("sort", "fresh")   # default: fresh first
        fresh_only = request.args.get("fresh_only", "")  # new: 24h filter

        filtered = all_jobs

        if fresh_only:
            filtered = [j for j in filtered if j.freshness_label in ("TODAY", "NEW")]
        if q:
            filtered = [j for j in filtered
                        if q in (j.title or "").lower()
                        or q in (j.company or "").lower()
                        or q in (j.location or "").lower()]
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

        FRESH_ORDER = {"TODAY": 0, "NEW": 1, "RECENT": 2, "OLD": 3}
        if sort == "score":
            filtered.sort(key=lambda j: j.ai_match_score or 0, reverse=True)
        elif sort == "company":
            filtered.sort(key=lambda j: (j.company or "").lower())
        else:  # fresh (default) — TODAY/NEW first, then newest date within each tier
            # Python sort is stable: sort by date desc first, then by tier asc (stable keeps date order)
            filtered.sort(key=lambda j: j.last_seen_at or "", reverse=True)
            filtered.sort(key=lambda j: FRESH_ORDER.get(j.freshness_label or "OLD", 3))

        return render_template("jobs.html",
                               jobs=filtered,
                               q=q,
                               freshness=freshness,
                               source=source,
                               min_score=min_score,
                               sort=sort,
                               fresh_only=fresh_only,
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
        # Clean applications — exclude "Already applied (detected on page)"
        rows = conn.execute(
            """
            SELECT a.*, j.title, j.company, j.url AS job_url, j.location
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            WHERE (a.error_reason NOT LIKE '%Already applied%' OR a.error_reason IS NULL)
            ORDER BY a.id DESC
            LIMIT 200
            """
        ).fetchall()
        apps = [dict(r) for r in rows]

        # Already-applied detected — for separate section
        det_rows = repo.get_already_applied_detected(limit=300)

        # Use clean summary for badge counts
        summary = repo.get_application_summary_clean()
        already_det_count = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE error_reason LIKE '%Already applied%'"
        ).fetchone()[0]

        return render_template(
            "applications.html",
            apps=apps,
            summary=summary,
            already_applied=det_rows,
            already_det_count=already_det_count,
        )
    finally:
        conn.close()


@app.route("/status")
def status_page():
    """Dedicated Application Status Tracker page."""
    conn, repo = get_repo()
    try:
        rows = conn.execute(
            """
            SELECT a.*, j.title, j.company, j.url AS job_url, j.location, j.ai_match_score, j.freshness_label
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            ORDER BY a.id DESC
            LIMIT 500
            """
        ).fetchall()
        apps = [dict(r) for r in rows]

        summary = repo.get_application_summary_clean()
        already_det_count = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE error_reason LIKE '%Already applied%'"
        ).fetchone()[0]

        global AGENT_LOOP_PROC
        is_auto_pilot_running = AGENT_LOOP_PROC is not None and AGENT_LOOP_PROC.poll() is None

        return render_template(
            "status.html",
            apps=apps,
            summary=summary,
            already_det_count=already_det_count,
            auto_pilot_running=is_auto_pilot_running,
            auto_pilot_pid=AGENT_LOOP_PROC.pid if is_auto_pilot_running else None,
        )
    finally:
        conn.close()


@app.route("/api/status/sync", methods=["POST"])
def api_status_sync():
    """Import LinkedIn status emails into the application tracker."""
    try:
        count, message = sync_linkedin_emails()
        return jsonify({"status": "success", "count": count, "message": message})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/profile", methods=["GET", "POST"])
def profile_page():
    profile_path = Path(SETTINGS.user_profile_path)
    if request.method == "POST":
        try:
            data = request.json or {}
            # Write back to JSON
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return jsonify({"status": "success", "message": "Profile saved successfully!"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # GET profile
    profile_data = {}
    if profile_path.exists():
        with open(profile_path, encoding="utf-8") as f:
            profile_data = json.load(f)
    return render_template("profile.html", profile=profile_data)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    env_path = Path(__file__).parent.parent / ".env"
    if request.method == "POST":
        try:
            data = request.json or {}
            # Read existing .env lines
            existing = {}
            if env_path.exists():
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            existing[k.strip()] = v.strip()

            # Update with submitted fields
            for key in ["AI_PROVIDER", "CLAUDE_API_KEY", "CLAUDE_MODEL",
                        "DEFAULT_WHAT", "DEFAULT_WHERE", "JOOBLE_API_KEY",
                        "AI_MATCH_THRESHOLD"]:
                if key in data:
                    existing[key] = str(data[key])

            # Write back to .env
            with open(env_path, "w", encoding="utf-8") as f:
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")

            return jsonify({"status": "success", "message": "Settings updated! (.env reloaded)"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # GET settings
    curr_settings = {
        "ai_provider": SETTINGS.ai_provider,
        "claude_api_key": SETTINGS.claude_api_key or "",
        "claude_model": SETTINGS.claude_model,
        "default_what": SETTINGS.default_what,
        "default_where": SETTINGS.default_where,
        "jooble_api_key": SETTINGS.jooble_api_key or "",
        "ai_match_threshold": SETTINGS.ai_match_threshold,
    }
    return render_template("settings.html", settings=curr_settings)


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
    """Trigger AI scoring in the background using the configured AI provider."""
    def _run():
        from ai import get_ai_client
        from ai.scorer import score_unanalyzed_jobs
        from config.profile_loader import CandidateProfile
        conn2, repo2 = get_repo()
        try:
            profile = CandidateProfile.load_from_file(SETTINGS.user_profile_path)
            client = get_ai_client()
            if client.health_check():
                score_unanalyzed_jobs(repo2, profile, client, limit=200, verbose=False)
        except Exception:
            pass
        finally:
            conn2.close()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "started"})



from flask import send_from_directory

@app.route("/screenshots/<path:filename>")
def serve_screenshot(filename):
    """Serve application screenshots from the screenshots directory."""
    screenshots_dir = Path(__file__).parent.parent / "applications" / "screenshots"
    return send_from_directory(screenshots_dir, filename)


@app.route("/api/applications/<int:app_id>")
def api_application_detail(app_id):
    """Return full application details including parsed logs for UI modal inspection."""
    conn, repo = get_repo()
    try:
        row = conn.execute(
            """
            SELECT a.*, j.title, j.company, j.url AS job_url, j.location, j.ai_match_score
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            WHERE a.id = ?
            """,
            (app_id,)
        ).fetchone()

        if not row:
            return jsonify({"error": "Application not found"}), 404

        d = dict(row)
        # Parse JSON logs string into Python list
        try:
            d["logs_parsed"] = json.loads(d.get("logs") or "[]")
        except Exception:
            d["logs_parsed"] = [d.get("logs") or "No detailed logs recorded."]

        # Standardize screenshot filename for web URL
        if d.get("screenshot_path"):
            sp = Path(d["screenshot_path"])
            d["screenshot_url"] = f"/screenshots/{sp.name}"
        else:
            d["screenshot_url"] = None

        return jsonify(d)
    finally:
        conn.close()


# Global reference to running agent_loop process
AGENT_LOOP_PROC = None

@app.route("/run/agent_loop", methods=["POST"])
def run_agent_loop_endpoint():
    """Start the fully automated fetch+score+apply loop in the background."""
    global AGENT_LOOP_PROC
    if AGENT_LOOP_PROC and AGENT_LOOP_PROC.poll() is None:
        return jsonify({"status": "already_running", "pid": AGENT_LOOP_PROC.pid})

    root_dir = Path(__file__).parent.parent
    AGENT_LOOP_PROC = subprocess.Popen(
        [sys.executable, "agent_loop.py"],
        cwd=str(root_dir),
    )
    return jsonify({"status": "started", "pid": AGENT_LOOP_PROC.pid})


@app.route("/run/agent_loop/stop", methods=["POST"])
def stop_agent_loop_endpoint():
    """Stop the background auto-pilot loop."""
    global AGENT_LOOP_PROC
    if AGENT_LOOP_PROC and AGENT_LOOP_PROC.poll() is None:
        AGENT_LOOP_PROC.terminate()
        try:
            AGENT_LOOP_PROC.wait(timeout=5)
        except Exception:
            AGENT_LOOP_PROC.kill()
        AGENT_LOOP_PROC = None
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})


@app.route("/api/agent_loop/status")
def agent_loop_status():
    """Check if the auto-pilot background process is currently running."""
    global AGENT_LOOP_PROC
    is_running = AGENT_LOOP_PROC is not None and AGENT_LOOP_PROC.poll() is None
    return jsonify({
        "running": is_running,
        "pid": AGENT_LOOP_PROC.pid if is_running else None,
    })


@app.route("/run/login", methods=["POST"])
def run_login():
    """Launch interactive browser login session (headful) in background."""
    def _run():
        subprocess.run([sys.executable, "auto_apply.py", "--login-only"], cwd=str(Path(__file__).parent.parent))
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Opening visible browser for login..."})


# ── Entry point ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
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
