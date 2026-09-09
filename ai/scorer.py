"""
scorer.py — Standalone AI scoring loop.

Loops through active jobs that have descriptions but no AI score yet,
calls the configured AI client (Claude or Ollama), and stores the result.

Usage:
    python main.py --score-only      # score via CLI
    from ai.scorer import score_unanalyzed_jobs  # programmatic use
"""

from config.settings import SETTINGS
from config.profile_loader import CandidateProfile
from database.models import init_db, get_connection
from database.repository import JobRepository
from ai.job_matcher import match_job


def score_unanalyzed_jobs(
    repo: JobRepository,
    profile: CandidateProfile,
    ai_client,           # OllamaClient or ClaudeClient — both share the same interface
    limit: int = 100,
    verbose: bool = True,
) -> dict:
    """
    Score all unanalyzed active jobs that have descriptions.
    Returns a summary dict {scored, skipped_no_desc, failed}.
    """
    jobs = repo.get_unanalyzed_jobs(limit=limit)
    if not jobs:
        if verbose:
            print("  [AI scorer] No unanalyzed jobs found.\n")
        return {"scored": 0, "skipped_no_desc": 0, "failed": 0}

    jobs_with_desc = [j for j in jobs if j.description and len(j.description) > 50]
    jobs_without   = [j for j in jobs if not j.description or len(j.description) <= 50]

    if verbose:
        print(f"  [AI scorer] Scoring {len(jobs_with_desc)} jobs "
              f"({len(jobs_without)} skipped — no description)...\n")

    scored = 0
    failed = 0

    for idx, job in enumerate(jobs_with_desc, 1):
        if verbose:
            title_short = (job.title[:50] + "…") if len(job.title) > 50 else job.title
            print(f"  [{idx}/{len(jobs_with_desc)}] {title_short}", end="", flush=True)
        try:
            result = match_job(
                client=ai_client,
                job_title=job.title,
                job_description=job.description,
                profile=profile,
            )
            repo.update_ai_analysis(
                job_id=job.id,
                score=result.match_score,
                recommendation=result.recommendation,
            )
            if verbose:
                badge = {
                    "STRONG_MATCH": "🟢",
                    "GOOD_MATCH":   "🔵",
                    "WEAK_MATCH":   "🟡",
                    "SKIP":         "🔴",
                }.get(result.recommendation, "⚪")
                print(f" → {result.match_score}% {badge} {result.recommendation}")
            scored += 1
        except Exception as exc:
            if verbose:
                print(f" → ❌ FAILED ({exc})")
            failed += 1

    if verbose:
        print(f"\n  [AI scorer] Done — {scored} scored, "
              f"{len(jobs_without)} skipped (no desc), {failed} failed.\n")

    return {"scored": scored, "skipped_no_desc": len(jobs_without), "failed": failed}


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("\n====================================")
    print("       AI JOB SCORER")
    print("====================================\n")

    profile_path = SETTINGS.user_profile_path
    try:
        profile = CandidateProfile.load_from_file(profile_path)
        print(f"  Profile : {profile.personal.full_name}")
    except Exception as e:
        print(f"  ERROR loading profile: {e}")
        sys.exit(1)

    from ai import get_ai_client
    try:
        ai_client = get_ai_client()
    except ValueError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    if not ai_client.health_check():
        provider = SETTINGS.ai_provider.upper()
        print(f"  ERROR: {provider} AI client not available. Check your .env configuration.")
        if SETTINGS.ai_provider == "ollama":
            print("  Start Ollama with: ollama serve")
        sys.exit(1)

    provider_label = (
        f"Claude ({SETTINGS.claude_model})"
        if SETTINGS.ai_provider == "claude"
        else f"Ollama ({SETTINGS.ollama_model})"
    )
    print(f"  Model   : {provider_label}")
    print(f"  Threshold: {SETTINGS.ai_match_threshold}%\n")

    init_db(SETTINGS.database_path)
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)

    score_unanalyzed_jobs(repo, profile, ai_client, limit=200)
    conn.close()
