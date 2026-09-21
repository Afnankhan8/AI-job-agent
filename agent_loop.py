"""
agent_loop.py — Fully Automatic AI Job Agent Runner.

Runs forever in the background. Each cycle:
  1. Fetch fresh jobs (LinkedIn, Jooble, Adzuna)
  2. Fetch missing descriptions
  3. AI score all unscored jobs (Claude)
  4. Auto-apply to the best fresh unapplied jobs (TODAY + NEW first)
  5. Sleep for LOOP_INTERVAL_MINUTES, then repeat

Usage:
  python agent_loop.py                  # run forever (headless)
  python agent_loop.py --headful        # show browser windows
  python agent_loop.py --dry-run        # fill forms, do NOT submit
  python agent_loop.py --min-score 60   # only apply if AI score >= 60
  python agent_loop.py --interval 30    # loop every 30 minutes (default: 45)
  python agent_loop.py --cycles 3       # run exactly 3 cycles then exit
  python agent_loop.py --once           # run a single cycle and exit
"""

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone

# Fix Windows console encoding
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore

from config.settings import SETTINGS
from database.models import init_db, get_connection
from database.repository import JobRepository


def ts() -> str:
    """Current timestamp string for log prefix."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def banner(text: str) -> None:
    w = 52
    print("\n" + "=" * w)
    print(f"  {text}")
    print("=" * w)


def run_cycle(
    headful: bool = False,
    dry_run: bool = False,
    min_score: int | None = None,
    apply_limit: int = 30,
    pages: int = 5,
    fresh_only: bool = True,
) -> dict:
    """
    Run one full pipeline cycle.
    Returns a summary dict with counts.
    """
    from main import run_ingestion, run_description_fetch, run_ai_scoring
    from auto_apply import run_auto_apply
    from email_tracker import sync_linkedin_emails

    summary = {
        "new_jobs": 0,
        "applied": 0,
        "skipped": 0,
        "errors": [],
    }

    # ── Step 1: Ingest fresh jobs ──────────────────────────────────────────────
    banner(f"[{ts()}] STEP 1 — FETCHING JOBS")
    try:
        new_jobs = run_ingestion(
            what=SETTINGS.default_what,
            where=SETTINGS.default_where,
            pages=pages,
        )
        summary["new_jobs"] = new_jobs
        print(f"\n  ✓ {new_jobs} new jobs ingested.")
    except Exception as e:
        msg = f"Ingestion error: {e}"
        print(f"  ✗ {msg}")
        summary["errors"].append(msg)

    # ── Step 2: Fetch missing descriptions ────────────────────────────────────
    banner(f"[{ts()}] STEP 2 — FETCHING DESCRIPTIONS")
    try:
        run_description_fetch(limit=200)
        print("  ✓ Descriptions updated.")
    except Exception as e:
        msg = f"Description fetch error: {e}"
        print(f"  ✗ {msg}")
        summary["errors"].append(msg)

    # ── Step 3: AI scoring ────────────────────────────────────────────────────
    banner(f"[{ts()}] STEP 3 — AI SCORING")
    try:
        run_ai_scoring()
        print("  ✓ AI scoring complete.")
    except Exception as e:
        msg = f"AI scoring error: {e}"
        print(f"  ✗ {msg}")
        summary["errors"].append(msg)

    # ── Step 4: Auto-apply ────────────────────────────────────────────────────
    banner(f"[{ts()}] STEP 4 — AUTO-APPLYING (fresh_only={fresh_only})")

    # Get count of eligible jobs before applying
    try:
        conn = get_connection(SETTINGS.database_path)
        repo = JobRepository(conn)
        eligible = repo.get_unapplied_jobs(limit=500, fresh_only=fresh_only)
        conn.close()
        print(f"\n  Eligible unapplied jobs: {len(eligible)}")
        if not eligible:
            print("  ✓ Nothing to apply — all fresh jobs already processed.")
    except Exception as e:
        print(f"  ✗ Could not count eligible jobs: {e}")

    try:
        run_auto_apply(
            limit=apply_limit,
            dry_run=dry_run,
            headful=headful,
            min_score=min_score if min_score is not None else SETTINGS.ai_match_threshold,
            fresh_only=fresh_only,
        )
        print("  ✓ Auto-apply cycle complete.")
    except Exception as e:
        msg = f"Auto-apply error: {e}"
        print(f"  ✗ {msg}")
        traceback.print_exc()
        summary["errors"].append(msg)

    # ── Step 5: Sync LinkedIn response emails ────────────────────────────────
    banner(f"[{ts()}] STEP 5 — SYNCING LINKEDIN RESPONSES")
    try:
        count, message = sync_linkedin_emails()
        print(f"  ✓ {message}.")
        summary["linkedin_responses"] = count
    except Exception as e:
        msg = f"LinkedIn email sync error: {e}"
        print(f"  ✗ {msg}")
        summary["errors"].append(msg)

    # ── Step 6: Remove unanswered real applications after three months ────────
    try:
        conn = get_connection(SETTINGS.database_path)
        repo = JobRepository(conn)
        cleanup = repo.clear_jobs(clear_applied=True, clear_old=True, unanswered_days=90)
        conn.close()
        print(
            f"  ✓ Cleanup: removed {cleanup['deleted_jobs']} jobs and "
            f"{cleanup['deleted_apps']} unanswered applications older than 90 days."
        )
    except Exception as e:
        msg = f"Cleanup error: {e}"
        print(f"  ✗ {msg}")
        summary["errors"].append(msg)

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Fully automatic AI Job Agent — fetch + score + apply loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--headful",    action="store_true", help="Show browser windows")
    parser.add_argument("--dry-run",    action="store_true", help="Fill forms, do NOT submit")
    parser.add_argument("--min-score",  type=int, default=None, help="Minimum AI score (default: from .env)")
    parser.add_argument("--interval",   type=int, default=45,   help="Minutes between cycles (default: 45)")
    parser.add_argument("--cycles",     type=int, default=None,  help="Number of cycles to run (default: infinite)")
    parser.add_argument("--once",       action="store_true",     help="Run a single cycle and exit")
    parser.add_argument("--pages",      type=int, default=5,     help="Pages to fetch per source (default: 5)")
    parser.add_argument("--limit",      type=int, default=30,    help="Max jobs to apply per cycle (default: 30)")
    parser.add_argument("--all-jobs",   action="store_true",     help="Apply to all ages, not just TODAY/NEW")
    args = parser.parse_args()

    if args.once:
        args.cycles = 1

    max_cycles = args.cycles  # None = infinite
    fresh_only = not args.all_jobs
    cycle_num = 0

    banner("AI JOB AGENT — FULLY AUTOMATIC MODE")
    print(f"""
  Mode       : {'DRY RUN (no submit)' if args.dry_run else 'LIVE (real applications)'}
  Browser    : {'Visible' if args.headful else 'Headless (background)'}
    Min Score  : {args.min_score if args.min_score is not None else SETTINGS.ai_match_threshold}%
  Fresh Only : {fresh_only} (only TODAY+NEW jobs)
  Apply Limit: {args.limit} jobs per cycle
  Interval   : every {args.interval} minutes
  Max Cycles : {max_cycles or 'infinite'}
  Keywords   : {SETTINGS.default_what}
  Location   : {SETTINGS.default_where}
""")

    # Initialise DB
    init_db(SETTINGS.database_path)

    while True:
        cycle_num += 1
        banner(f"CYCLE {cycle_num}{f'/{max_cycles}' if max_cycles else ''} — {ts()}")

        try:
            summary = run_cycle(
                headful=args.headful,
                dry_run=args.dry_run,
                min_score=args.min_score,
                apply_limit=args.limit,
                pages=args.pages,
                fresh_only=fresh_only,
            )
        except Exception as e:
            print(f"\n  ✗ CYCLE CRASHED: {e}")
            traceback.print_exc()

        if max_cycles and cycle_num >= max_cycles:
            banner("ALL CYCLES COMPLETE")
            break

        # Sleep until next cycle
        next_run = datetime.now().strftime("%H:%M:%S")
        print(f"\n  ✓ Cycle {cycle_num} done. Next run in {args.interval} minutes.")
        print(f"  Sleeping until ~{next_run} + {args.interval}min…")
        print("  (Press Ctrl+C to stop)")

        try:
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            banner("AGENT STOPPED BY USER")
            break


if __name__ == "__main__":
    main()
