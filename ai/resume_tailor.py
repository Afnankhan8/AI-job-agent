"""
AI Resume Tailor.

Given a job posting and the candidate's profile, uses Ollama/Qwen to generate:
  1. A personalized cover letter tailored to this specific job
  2. Key selling points to highlight
  3. Pre-answered screening questions

Usage:
    from ai.resume_tailor import tailor_application
    from ai.ollama_client import OllamaClient

    client = OllamaClient()
    result = tailor_application(client, job_title, job_description, profile)
    print(result.cover_letter)
    print(result.suggested_answers)
"""

from dataclasses import dataclass, field
from typing import Dict, List

from ai.ollama_client import OllamaClient
from config.profile_loader import CandidateProfile


@dataclass
class TailoredApplication:
    cover_letter: str                          # personalized cover letter
    key_points: List[str]                      # bullet points of why candidate fits
    suggested_answers: Dict[str, str]          # pre-answered screening questions


SYSTEM_PROMPT = """You are a professional job application writer. Your task is to create a personalized, compelling job application.

You MUST respond with ONLY a valid JSON object, no other text. Do not wrap in markdown fences. Do not add explanations outside the JSON.

The JSON must have exactly these keys:
- "cover_letter": a 3-4 paragraph professional cover letter (as a single string with \\n for line breaks). Address it to "Dear Hiring Team". Sign it with the candidate's name. Make it specific to the job, not generic.
- "key_points": an array of 3-5 short bullet points explaining why this candidate is a good fit for THIS specific job
- "suggested_answers": an object with common screening question answers, including:
  - "why_interested": why you're interested in this role (2-3 sentences)
  - "relevant_experience": your most relevant experience (2-3 sentences)
  - "greatest_strength": your greatest strength for this role (1-2 sentences)

Write naturally and professionally. Avoid buzzwords and filler. Be specific about the candidate's actual skills and experience."""


def tailor_application(
    client: OllamaClient,
    job_title: str,
    job_description: str,
    profile: CandidateProfile,
) -> TailoredApplication:
    """
    Generate a tailored application package for a specific job.
    Returns cover letter, key selling points, and pre-answered questions.
    """
    candidate_summary = profile.to_summary()

    prompt = f"""Create a tailored job application for this candidate and job.

=== JOB POSTING ===
Title: {job_title}
Description:
{job_description[:3000]}

=== CANDIDATE PROFILE ===
{candidate_summary}

=== INSTRUCTIONS ===
Write a cover letter that:
1. Mentions the specific job title
2. Connects the candidate's actual skills and experience to the job requirements
3. Is professional but not generic — it should feel written for THIS job
4. Is 3-4 paragraphs, not too long

Respond with ONLY a JSON object. No markdown fences. No extra text."""

    try:
        data = client.generate_json(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.7,
        )

        return TailoredApplication(
            cover_letter=data.get("cover_letter", profile.cover_letter_template),
            key_points=data.get("key_points", []),
            suggested_answers=data.get("suggested_answers", {}),
        )

    except (ValueError, RuntimeError) as exc:
        # Fallback to the static template if AI fails
        return TailoredApplication(
            cover_letter=profile.cover_letter_template,
            key_points=[f"AI tailoring failed: {exc}. Using default cover letter."],
            suggested_answers={},
        )
