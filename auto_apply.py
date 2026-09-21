"""
auto_apply.py — AI-Powered Auto-Apply Runner (v2).

Full pipeline per job:
  1. Upfront session management   — login once, reuse cookies across all jobs
  2. Filter by AI score           — only process jobs above threshold
  3. AI cover letter generation   — tailored per job (requires description)
  4. Platform detection           — LinkedIn / Greenhouse / Lever / Workday / Generic
  5. Adapter dispatch             — platform-specific form filling + submission
  6. Record outcome in DB         — APPLIED / DRY_RUN / REQUIRES_MANUAL / FAILED

Usage:
  python auto_apply.py                  # apply to all unapplied jobs
  python auto_apply.py --dry-run        # fill forms, do NOT submit
  python auto_apply.py --headful        # show browser window
  python auto_apply.py --limit 10       # process at most 10 jobs
  python auto_apply.py --skip-ai        # skip AI scoring/cover letter
  python auto_apply.py --min-score 70   # only apply to jobs scored >= 70%
  python auto_apply.py --login-only     # just establish session, then exit

Or triggered from main.py via --auto-apply / --login-only flags.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Fix Windows console encoding
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore

from config.settings import SETTINGS
from config.profile_loader import CandidateProfile
from database.models import init_db, get_connection
from database.repository import JobRepository
from applier.session_manager import SessionManager
from applier.browser_utils import launch_safe_context
from applier.adapters import detect_platform, get_adapter
from applier.url_resolver import resolve_url
from ai import get_ai_client
from ai.resume_tailor import tailor_application
from jobs.scope import check_job_scope
from email_tracker import sync_linkedin_emails


# ── Session-only entry point ───────────────────────────────────────────────────

def run_login_only(headful: bool = True, profile_path: str | None = None) -> None:
    """
    Open the browser, log into all platforms, then exit.
    Run this once to establish persistent sessions — subsequent runs
    skip the login page entirely.
    """
    profile_path = profile_path or SETTINGS.user_profile_path
    try:
        profile = CandidateProfile.load_from_file(profile_path)
    except Exception as e:
        print(f"  ERROR loading profile: {e}")
        sys.exit(1)

    print("\n====================================")
    print("     SESSION LOGIN")
    print("====================================")
    print(f"  Profile : {profile.personal.full_name}")
    print(f"  LinkedIn: {profile.personal.linkedin_email}")
    print(f"  Browser : {'Visible' if headful else 'Headless'}")
    print("====================================\n")

    with sync_playwright() as p:
        ctx, _ = launch_safe_context(
            p,
            user_data_dir=Path("browser_profile"),
            headless=not headful,
        )

        manager = SessionManager(ctx, profile, verbose=True)
        results = manager.ensure_all_sessions()

        for platform, ok in results.items():
            status = "LOGGED IN" if ok else "FAILED"
            print(f"  {platform}: {status}")

        ctx.close()

    if results.get("linkedin"):
        print("\n  LinkedIn session saved. Future runs will skip the login page.\n")
    else:
        print("\n  LinkedIn login was not completed. No session was saved.\n")
        sys.exit(1)


# ── Main apply runner ──────────────────────────────────────────────────────────

def run_auto_apply(
    limit: int | None = None,
    dry_run: bool = False,
    headful: bool = False,
    profile_path: str | None = None,
    skip_ai: bool = False,
    min_score: int | None = None,
    fresh_only: bool = True,
):
    """
    Auto-apply to unapplied jobs using platform adapters.
    """
    apply_limit = limit or 30
    min_score = min_score if min_score is not None else SETTINGS.ai_match_threshold
    headless = not headful
    profile_path = profile_path or SETTINGS.user_profile_path

    try:
        profile = CandidateProfile.load_from_file(profile_path)
    except Exception as e:
        print(f"  ERROR loading profile: {e}")
        sys.exit(1)

    print("\n====================================")
    print("     AI AUTO-APPLY ENGINE v2")
    print("====================================")
    print(f"  Profile   : {profile.personal.full_name}")
    print(f"  Mode      : {'DRY RUN (fill only)' if dry_run else 'LIVE APPLY (will submit)'}")
    print(f"  Browser   : {'Visible' if headful else 'Headless'}")
    print(f"  Min Score : {min_score}%")
    print(f"  Fresh Only: {fresh_only}")

    ai_client = None
    if not skip_ai:
        try:
            ai_client = get_ai_client()
            if ai_client and ai_client.is_available():
                client_name = type(ai_client).__name__.replace("Client", "")
                model = (
                    SETTINGS.claude_model
                    if SETTINGS.ai_provider == "claude"
                    else SETTINGS.ollama_model
                )
                print(f"  AI Engine : {client_name} connected ({model})\n")
            else:
                print(f"  AI Engine : AI not available — falling back to static profile.\n")
                ai_client = None
        except Exception as e:
            print(f"  AI Engine : Could not initialise — {e}. Falling back to static profile.\n")
            ai_client = None

    # ── Database ──────────────────────────────────────────────────────────────
    init_db(SETTINGS.database_path)
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)
    
    # Prune old jobs that we no longer care about
    pruned_count = repo.prune_expired_jobs(days=30)
    if pruned_count > 0:
        print(f"  [Database] Pruned {pruned_count} expired jobs from the active queue.")

    cleanup = repo.clear_jobs(clear_applied=True, clear_old=True, unanswered_days=90)
    if cleanup["deleted_jobs"] or cleanup["deleted_apps"]:
        print(
            f"  [Database] Removed {cleanup['deleted_jobs']} jobs and "
            f"{cleanup['deleted_apps']} unanswered applications older than 90 days."
        )

    # Get unapplied jobs — prefer those with AI scores above threshold
    all_unapplied = [
        job for job in repo.get_unapplied_jobs(
            limit=apply_limit * 3, fresh_only=fresh_only
        )
        if job.source_name != "jooble"
    ]

    # Filter by score if AI has scored them; include unscored jobs too
    jobs_to_process = []
    for j in all_unapplied:
        scope = check_job_scope(j, profile, SETTINGS.default_where)
        if not scope.allowed:
            print(f"         Scope skip: {j.title} — {scope.reason}")
            continue
        if j.ai_match_score is not None:
            if j.ai_match_score >= min_score:
                jobs_to_process.append(j)
        else:
            # Unscored job — include it (will try to score inline if AI available)
            jobs_to_process.append(j)

    # Sort: Newest seen first (last_seen_at DESC), then scored jobs
    jobs_to_process.sort(
        # Process direct LinkedIn listings before aggregator pages that may
        # be stopped by an external CAPTCHA/Cloudflare challenge.
        key=lambda j: (
            0 if j.source_name == "linkedin" else 1,
            j.last_seen_at,
            j.ai_match_score is None,
            -(j.ai_match_score or 0),
        ),
        reverse=True
    )
    jobs_to_process = jobs_to_process[:apply_limit]

    if not jobs_to_process:
        print("  No unapplied jobs to process.\n")
        print("  Tip: Run 'python main.py' first to fetch and score jobs.\n")
        conn.close()
        return

    print(f"  Processing {len(jobs_to_process)} job(s).\n")

    counts = {"APPLIED": 0, "DRY_RUN": 0, "REQUIRES_MANUAL": 0, "FAILED": 0, "SKIPPED": 0}
    manual_jobs = []

    # ── Browser context ───────────────────────────────────────────────────────
    with sync_playwright() as p:
        ctx, _ = launch_safe_context(
            p,
            user_data_dir=Path("browser_profile"),
            headless=headless,
        )

        # ── Step 1: Ensure all platform sessions (login upfront) ───────────────
        print("  [1/4] Checking platform sessions...")
        session_mgr = SessionManager(ctx, profile, verbose=True)
        session_results = session_mgr.ensure_all_sessions()
        for platform, ok in session_results.items():
            print(f"         {platform}: {'OK' if ok else 'FAILED (will retry per-job)'}")
        print()

        # ── Step 2-4: Apply loop ───────────────────────────────────────────────
        print(f"  [2/4] Generating AI cover letters and applying...\n")

        for idx, job in enumerate(jobs_to_process, 1):
            title_short = (job.title[:55] + "...") if len(job.title) > 55 else job.title
            print(f"  [{idx}/{len(jobs_to_process)}] {title_short}")
            print(f"         {job.company or 'Unknown'} | {job.location or 'Unknown'}")

            # Show AI score
            if job.ai_match_score is not None:
                badge = {"STRONG_MATCH": "[STRONG]", "GOOD_MATCH": "[GOOD]",
                         "WEAK_MATCH": "[WEAK]", "SKIP": "[SKIP]"}.get(job.ai_recommendation, "")
                print(f"         AI score: {job.ai_match_score}% {badge}")
            else:
                print(f"         AI score: Not scored")

            # ── Inline AI scoring for unscored jobs ────────────────────────────
            ai_cover_letter = job.ai_cover_letter
            ai_answers = {}

            if ai_client and job.description and len(job.description) > 50:
                if job.ai_match_score is None:
                    # Score it now
                    try:
                        from ai.job_matcher import match_job
                        match = match_job(ai_client, job.title, job.description, profile)
                        repo.update_ai_analysis(job.id, match.match_score, match.recommendation)
                        # Update the local object so remaining logic sees the score
                        job.ai_match_score = match.match_score
                        job.ai_recommendation = match.recommendation
                        if match.match_score < min_score:
                            print(f"         Score {match.match_score}% below threshold {min_score}% — SKIPPING")
                            counts["SKIPPED"] += 1
                            print()
                            continue
                        print(f"         AI scored inline: {match.match_score}% {match.recommendation}")
                    except Exception as e:
                        print(f"         AI scoring failed: {e}")


                # Generate cover letter if not already done
                if not ai_cover_letter:
                    try:
                        print(f"         Generating cover letter...", end="", flush=True)
                        tailored = tailor_application(
                            ai_client, job.title, job.description, profile
                        )
                        ai_cover_letter = tailored.cover_letter
                        ai_answers = tailored.suggested_answers or {}
                        repo.update_ai_analysis(
                            job_id=job.id,
                            score=job.ai_match_score or 0,
                            recommendation=job.ai_recommendation or "UNSCORED",
                            cover_letter=ai_cover_letter,
                        )
                        print(f" done ({len(ai_cover_letter)} chars)")
                    except Exception as e:
                        print(f" failed ({e})")
                        ai_cover_letter = profile.cover_letter_template
            elif not ai_cover_letter:
                ai_cover_letter = profile.cover_letter_template

            # ── Detect platform ────────────────────────────────────────────────
            resolved_url, _ = resolve_url((job.url or ""))
            platform = detect_platform(resolved_url)
            adapter  = get_adapter(platform)
            print(f"         Platform: {platform.upper()} | URL: {resolved_url[:70]}...")

            # ── Create application record ──────────────────────────────────────
            app_id = repo.create_application(job.id, status="PENDING")

            # ── Apply ──────────────────────────────────────────────────────────
            try:
                # Pass session_manager to LinkedIn adapter so it can auto re-login
                extra_kwargs = {}
                if platform == "linkedin":
                    extra_kwargs["_session_manager"] = session_mgr

                outcome = adapter.apply(
                    job=job,
                    profile=profile,
                    context=ctx,
                    dry_run=dry_run,
                    ai_cover_letter=ai_cover_letter,
                    ai_answers=ai_answers,
                    screenshots_dir=SETTINGS.screenshots_dir,
                    ai_client=ai_client,
                    **extra_kwargs,
                )
            except Exception as e:
                print(f"         ADAPTER ERROR: {e}")
                outcome_status = "FAILED"
                repo.update_application(
                    app_id=app_id, status="FAILED",
                    redirect_url=resolved_url, error_reason=str(e), logs=[str(e)],
                )
                counts["FAILED"] += 1
                print()
                continue

            # ── Record result ──────────────────────────────────────────────────
            repo.update_application(
                app_id=app_id,
                status=outcome.status,
                redirect_url=outcome.redirect_url,
                screenshot_path=outcome.screenshot_path,
                error_reason=outcome.error_reason,
                logs=outcome.logs,
                ai_match_score=job.ai_match_score,
                ai_cover_letter=ai_cover_letter,
            )

            counts[outcome.status] = counts.get(outcome.status, 0) + 1

            # Print outcome
            status_labels = {
                "APPLIED":          "  APPLIED",
                "DRY_RUN":          "  FILLED (dry-run)",
                "REQUIRES_MANUAL":  "  MANUAL NEEDED",
                "FAILED":           "  FAILED",
            }
            print(f"         {status_labels.get(outcome.status, outcome.status)}")
            if outcome.error_reason:
                print(f"         Note: {outcome.error_reason[:120]}")
            if outcome.screenshot_path:
                print(f"         Screenshot: {outcome.screenshot_path}")
            if outcome.status == "REQUIRES_MANUAL":
                manual_jobs.append(outcome)
            print()

        ctx.close()
    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("====================================")
    print("     AUTO-APPLY SUMMARY")
    print("====================================")
    print(f"  Applied (submitted)  : {counts.get('APPLIED', 0)}")
    print(f"  Filled (dry-run)     : {counts.get('DRY_RUN', 0)}")
    print(f"  Needs manual action  : {counts.get('REQUIRES_MANUAL', 0)}")
    print(f"  Failed               : {counts.get('FAILED', 0)}")
    print(f"  Skipped (score)      : {counts.get('SKIPPED', 0)}")
    print("====================================\n")

    if manual_jobs:
        print("  Jobs that need manual attention:\n")
        for o in manual_jobs:
            print(f"    -> {o.redirect_url}")
        print()

    # ── Email confirmation sync ───────────────────────────────────────────────
    applied_count = counts.get("APPLIED", 0)
    if applied_count > 0:
        print("  [3/4] Syncing LinkedIn confirmation emails...\n")
        try:
            synced, msg = sync_linkedin_emails()
            print(f"         {msg}\n")
        except Exception as e:
            synced = 0
            print(f"         Email sync failed: {e}")
            print("         (Set IMAP_HOST / IMAP_USERNAME / IMAP_PASSWORD in .env to enable)\n")

        # ── Show per-job email confirmation status ────────────────────────────
        print("====================================")
        print("     EMAIL CONFIRMATION STATUS")
        print("====================================")

        try:
            init_db(SETTINGS.database_path)
            email_conn = get_connection(SETTINGS.database_path)
            email_rows = email_conn.execute(
                """
                SELECT j.title, j.company, a.status, a.applied_at,
                       a.response_status, a.response_subject, a.response_received_at
                FROM applications a
                JOIN jobs j ON j.id = a.job_id
                WHERE a.status = 'APPLIED'
                ORDER BY a.applied_at DESC
                LIMIT 30
                """
            ).fetchall()

            confirmed = 0
            awaiting  = 0
            for row in email_rows:
                title   = (row["title"][:45] + "...") if len(row["title"]) > 45 else row["title"]
                company = row["company"] or "Unknown"
                resp    = row["response_status"] or "AWAITING_REPLY"

                if resp == "APPLICATION_CONFIRMED":
                    subj = (row["response_subject"] or "")[:60]
                    print(f'  ✅ {title} @ {company}')
                    print(f'     Email: "{subj}"')
                    confirmed += 1
                elif resp in ("INTERVIEW_OR_NEXT_STEP",):
                    print(f'  🎉 {title} @ {company} — INTERVIEW / NEXT STEP!')
                    confirmed += 1
                elif resp == "REJECTED":
                    print(f'  ❌ {title} @ {company} — Rejected')
                else:
                    print(f'  ⏳ {title} @ {company} — Awaiting confirmation email...')
                    awaiting += 1

            print()
            print(f"  Confirmed via email : {confirmed}")
            print(f"  Awaiting reply      : {awaiting}")
            print("====================================\n")

            if awaiting > 0 and synced == 0:
                print("  💡 Tip: If you applied just now, confirmation emails may take a few minutes.")
                print("     Re-run 'python email_tracker.py' later to check for new confirmations.\n")

            email_conn.close()
        except Exception as e:
            print(f"  Could not read confirmation status: {e}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI-powered auto-apply engine v2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit",      type=int, default=None,  help="Max jobs to process")
    parser.add_argument("--dry-run",    action="store_true",     help="Fill forms but do NOT submit")
    parser.add_argument("--headful",    action="store_true",     help="Show browser window")
    parser.add_argument("--profile",    type=str, default=None,  help="Path to user_profile.json")
    parser.add_argument("--skip-ai",    action="store_true",     help="Skip AI analysis/cover letter")
    parser.add_argument("--min-score",  type=int, default=None,  help="Minimum AI match score (default: from .env)")
    parser.add_argument("--login-only", action="store_true",     help="Just log in to all platforms and exit")

    args = parser.parse_args()

    if args.login_only:
        run_login_only(headful=True, profile_path=args.profile)
    else:
        run_auto_apply(
            limit=args.limit,
            dry_run=args.dry_run,
            headful=args.headful,
            profile_path=args.profile,
            skip_ai=args.skip_ai,
            min_score=args.min_score,
        )
