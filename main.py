"""
main.py — Job Ingestion & Auto-Apply Entry Point.

Usage examples:
  # Fetch jobs and display them
  python main.py

  # Fetch jobs from multiple keywords, 5 pages each
  python main.py --what "AI Engineer, ML Engineer, Data Scientist" --where UAE --pages 5

  # Fetch AND auto-apply (dry run — fills forms but does not click Submit)
  python main.py --auto-apply --dry-run

  # Fetch AND auto-apply for real, visible browser
  python main.py --auto-apply --headful

  # Just log into LinkedIn (one-time session setup, then future runs skip login)
  python main.py --login-only

  # Just apply to whatever is already in the database
  python auto_apply.py
"""

import argparse
import sys
from datetime import datetime, timezone

from config.settings import SETTINGS
from database.models import init_db, get_connection
from database.repository import JobRepository
from job_sources.adzuna import AdzunaConnector
from job_sources.jooble import JoobleConnector
from job_sources.linkedin import LinkedInConnector
from job_sources.base import JobSourceError, JobSourceConnector
from job_sources.description_fetcher import fetch_missing_descriptions
from jobs.normalizer import normalize_job
from jobs.freshness import classify_freshness
from jobs.deduplicator import check_duplicate


# ── Connector setup ────────────────────────────────────────────────────────────

def build_connectors() -> list[JobSourceConnector]:
    connectors = []

    if SETTINGS.adzuna_app_id and SETTINGS.adzuna_app_key:
        connectors.append(
            AdzunaConnector(SETTINGS.adzuna_app_id, SETTINGS.adzuna_app_key)
        )
    else:
        print("  [adzuna] skipped — no credentials in .env")

    if SETTINGS.jooble_api_key:
        connectors.append(JoobleConnector(SETTINGS.jooble_api_key))
    else:
        print("  [jooble] skipped — no credentials in .env")

    connectors.append(LinkedInConnector())

    if not connectors:
        print("ERROR: No job sources configured. Check your .env file.")
        sys.exit(1)

    return connectors


def _fetch(connector: JobSourceConnector, what: str, where: str, page: int):
    """Call the connector with the right signature."""
    if isinstance(connector, AdzunaConnector):
        return connector.fetch_jobs(
            what=what, where=where, page=page,
            country=SETTINGS.default_country,
        )
    return connector.fetch_jobs(what=what, where=where, page=page)


# ── Ingestion ──────────────────────────────────────────────────────────────────

def run_ingestion(what: str, where: str, pages: int) -> int:
    """
    Fetch jobs for every keyword/page combination and store them in the database.
    Returns the total number of new jobs inserted.
    """
    # Support comma-separated keywords, e.g. "AI Engineer, ML Engineer"
    keywords = [kw.strip() for kw in what.split(",") if kw.strip()]

    init_db(SETTINGS.database_path)
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)
    connectors = build_connectors()

    totals = {"inserted": 0, "updated": 0, "failed": 0}
    per_source: dict[str, dict] = {}

    print(f"\n  Keywords : {', '.join(keywords)}")
    print(f"  Location : {where}")
    print(f"  Pages    : {pages} per keyword per source")
    print(f"  Sources  : {', '.join(c.name for c in connectors)}\n")

    for kw in keywords:
        for connector in connectors:
            src = connector.name
            if src not in per_source:
                per_source[src] = {"inserted": 0, "updated": 0, "failed": 0}

            for page in range(1, pages + 1):
                try:
                    raw_jobs = _fetch(connector, kw, where, page)
                except JobSourceError as e:
                    print(f"    [{src}] notice on '{kw}' p{page}: {e.message}")
                    per_source[src]["failed"] += 1
                    totals["failed"] += 1
                    break   # stop paging this source/keyword on error

                if not raw_jobs:
                    break   # no more pages

                for raw in raw_jobs:
                    norm = normalize_job(raw)
                    decision = check_duplicate(norm, repo)
                    freshness = classify_freshness(
                        created_at_source=norm.created_at_source,
                        first_seen_at=datetime.now(timezone.utc),
                    )
                    if decision.is_duplicate:
                        repo.mark_seen_again(decision.existing_job_id, norm.source_name, freshness)
                        per_source[src]["updated"] += 1
                        totals["updated"] += 1
                    else:
                        repo.insert_job(norm, freshness)
                        per_source[src]["inserted"] += 1
                        totals["inserted"] += 1

    conn.close()

    # Summary
    print("====================================")
    print("     JOB INGESTION — SUMMARY")
    print("====================================")
    for src, stats in per_source.items():
        print(f"  [{src}]  new: {stats['inserted']}  updated: {stats['updated']}  failed: {stats['failed']}")
    print(f"  TOTAL new jobs  : {totals['inserted']}")
    print(f"  TOTAL updated   : {totals['updated']}")
    print("====================================\n")

    return totals["inserted"]


def run_description_fetch(limit: int = 200) -> None:
    """Fetch missing descriptions for active jobs (post-ingestion step)."""
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)
    fetch_missing_descriptions(repo, max_workers=8, limit=limit)
    conn.close()


def run_ai_scoring() -> None:
    """Score unanalyzed jobs with Ollama (skipped silently if Ollama is offline)."""
    from ai.ollama_client import OllamaClient
    from ai.scorer import score_unanalyzed_jobs
    from config.profile_loader import CandidateProfile

    try:
        profile = CandidateProfile.load_from_file(SETTINGS.user_profile_path)
    except Exception as e:
        print(f"  [AI scorer] Skipped — could not load profile: {e}")
        return

    ai_client = OllamaClient(
        base_url=SETTINGS.ollama_base_url,
        model=SETTINGS.ollama_model,
    )
    if not ai_client.health_check():
        print(f"  [AI scorer] Skipped — Ollama not reachable at {SETTINGS.ollama_base_url}")
        print("  Run 'ollama serve' and 'ollama pull qwen3-coder:30b' to enable AI scoring.\n")
        return

    print(f"  [AI scorer] Ollama connected ({SETTINGS.ollama_model}) — scoring jobs...\n")
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)
    score_unanalyzed_jobs(repo, profile, ai_client, limit=200)
    conn.close()


# ── Display ────────────────────────────────────────────────────────────────────

def print_jobs(limit: int = 50) -> None:
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)
    jobs = repo.list_active_jobs(limit=limit)
    conn.close()

    print(f"  {len(jobs)} active jobs in database:\n")
    for j in jobs:
        age   = f"[{j.freshness_label or '?':>6}]"
        title = j.title
        co    = j.company or "Unknown company"
        loc   = j.location or "Unknown location"
        src   = ", ".join(j.found_on_sources)
        print(f"  {age} {title}")
        print(f"           {co} | {loc} | via {src}")
        print(f"           {j.url}")
        print()


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI Job Agent — fetch jobs and optionally auto-apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --what "AI Engineer, ML Engineer" --where UAE --pages 5
  python main.py --auto-apply --dry-run
  python main.py --auto-apply --headful --limit 20
        """,
    )
    parser.add_argument(
        "--what", default=SETTINGS.default_what,
        help="Job keywords, comma-separated (default: from .env)",
    )
    parser.add_argument(
        "--where", default=SETTINGS.default_where,
        help="Location (default: from .env)",
    )
    parser.add_argument(
        "--pages", type=int, default=5,
        help="Pages to fetch per keyword per source (default: 5 = up to 100 jobs per source)",
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max jobs to display / auto-apply (default: 100)",
    )
    parser.add_argument(
        "--auto-apply", action="store_true",
        help="Auto-apply to unapplied jobs after ingestion",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fill forms but do NOT click the final Submit button",
    )
    parser.add_argument(
        "--headful", action="store_true",
        help="Show the browser window (useful for debugging)",
    )
    parser.add_argument(
        "--apply-only", action="store_true",
        help="Skip ingestion, only run auto-apply on existing database",
    )
    parser.add_argument(
        "--no-descriptions", action="store_true",
        help="Skip fetching job descriptions (faster, but disables AI scoring)",
    )
    parser.add_argument(
        "--score-only", action="store_true",
        help="Skip ingestion, only run AI scoring on existing jobs",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="Skip AI scoring step",
    )
    parser.add_argument(
        "--login-only", action="store_true",
        help="Log into LinkedIn (and other platforms) and save the session. Subsequent runs skip login.",
    )
    parser.add_argument(
        "--min-score", type=int, default=None,
        help="Minimum AI match score for auto-apply (default: from .env / settings)",
    )

    args = parser.parse_args()

    print("\n====================================")
    print("       AI JOB AGENT")
    print("====================================")

    # ── Login-only mode ────────────────────────────────────────────────────────
    if args.login_only:
        from auto_apply import run_login_only
        run_login_only(headful=True)
        sys.exit(0)

    if args.score_only:
        # Just run AI scoring on whatever is in the DB already
        init_db(SETTINGS.database_path)
        run_ai_scoring()
        print_jobs(limit=args.limit)
        sys.exit(0)

    if not args.apply_only:
        run_ingestion(what=args.what, where=args.where, pages=args.pages)

        if not args.no_descriptions:
            run_description_fetch(limit=200)

        if not args.no_ai:
            run_ai_scoring()

    print_jobs(limit=args.limit)

    if args.auto_apply or args.apply_only:
        from auto_apply import run_auto_apply
        run_auto_apply(
            limit=args.limit,
            dry_run=args.dry_run,
            headful=args.headful,
            min_score=getattr(args, 'min_score', None),
        )
