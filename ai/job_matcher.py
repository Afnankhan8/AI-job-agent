"""
AI Job Matcher.

Takes a job description and a candidate profile, sends them to Ollama/Qwen,
and returns a structured match analysis: score, matched/missing skills,
and a recommendation (STRONG_MATCH / GOOD_MATCH / WEAK_MATCH / SKIP).

Usage:
    from ai.job_matcher import match_job
    from ai.ollama_client import OllamaClient

    client = OllamaClient()
    result = match_job(client, job_title, job_description, profile)
    print(result.match_score)       # 87
    print(result.recommendation)    # "STRONG_MATCH"
"""

from dataclasses import dataclass, field
from typing import List

from ai.ollama_client import OllamaClient
from config.profile_loader import CandidateProfile


@dataclass
class JobMatchResult:
    match_score: int                    # 0–100
    matched_skills: List[str]           # skills the candidate has
    missing_skills: List[str]           # skills the job wants but candidate lacks
    recommendation: str                 # STRONG_MATCH | GOOD_MATCH | WEAK_MATCH | SKIP
    reasoning: str                      # one-paragraph explanation


SYSTEM_PROMPT = """You are a job matching AI assistant. Your task is to analyze how well a candidate's profile matches a job posting.

You MUST respond with ONLY a valid JSON object, no other text. Do not wrap in markdown. Do not add explanations outside the JSON.

The JSON must have exactly these keys:
- "match_score": integer 0 to 100
- "matched_skills": array of strings (skills the candidate has that the job requires)
- "missing_skills": array of strings (skills the job requires that the candidate lacks)
- "recommendation": one of "STRONG_MATCH", "GOOD_MATCH", "WEAK_MATCH", or "SKIP"
- "reasoning": a short paragraph explaining the match

Scoring guide:
- 80-100 = STRONG_MATCH (great fit, should definitely apply)
- 60-79  = GOOD_MATCH (decent fit, worth applying)
- 40-59  = WEAK_MATCH (some overlap, apply if nothing better)
- 0-39   = SKIP (poor fit, don't waste time)"""


def match_job(
    client: OllamaClient,
    job_title: str,
    job_description: str,
    profile: CandidateProfile,
) -> JobMatchResult:
    """
    Analyze how well the candidate matches a specific job.
    Returns a JobMatchResult with score and recommendation.
    """
    candidate_summary = profile.to_summary()

    prompt = f"""Analyze the match between this candidate and job posting.

=== JOB POSTING ===
Title: {job_title}
Description:
{job_description[:3000]}

=== CANDIDATE PROFILE ===
{candidate_summary}

Respond with ONLY a JSON object. No markdown fences. No extra text."""

    try:
        data = client.generate_json(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
        )

        # Validate and clamp score
        score = max(0, min(100, int(data.get("match_score", 0))))

        # Derive recommendation from score if model didn't provide one
        rec = data.get("recommendation", "")
        if rec not in ("STRONG_MATCH", "GOOD_MATCH", "WEAK_MATCH", "SKIP"):
            if score >= 80:
                rec = "STRONG_MATCH"
            elif score >= 60:
                rec = "GOOD_MATCH"
            elif score >= 40:
                rec = "WEAK_MATCH"
            else:
                rec = "SKIP"

        return JobMatchResult(
            match_score=score,
            matched_skills=data.get("matched_skills", []),
            missing_skills=data.get("missing_skills", []),
            recommendation=rec,
            reasoning=data.get("reasoning", "No reasoning provided."),
        )

    except (ValueError, RuntimeError) as exc:
        # If AI fails, return a conservative result rather than crashing
        return JobMatchResult(
            match_score=50,
            matched_skills=[],
            missing_skills=[],
            recommendation="WEAK_MATCH",
            reasoning=f"AI analysis failed: {exc}. Defaulting to WEAK_MATCH to allow manual review.",
        )
