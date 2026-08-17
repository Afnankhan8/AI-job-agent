"""
test_ai_engine.py — End-to-End AI Engine Test.

Tests the complete flow:
  1. Ollama connectivity
  2. Job matching (sends a real job description to Qwen)
  3. Application tailoring (generates cover letter)
  4. Prints all results so you can see the AI working

Run:
  python test_ai_engine.py
"""

import sys
import os

# Fix Windows console encoding for unicode characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from ai.ollama_client import OllamaClient
from ai.job_matcher import match_job
from ai.resume_tailor import tailor_application
from config.profile_loader import CandidateProfile
from config.settings import SETTINGS


# ── Sample job description for testing ────────────────────────────────────────

SAMPLE_JOB_TITLE = "AI Engineer"

SAMPLE_JOB_DESCRIPTION = """
We are looking for an AI Engineer to join our growing team in Dubai, UAE.

Responsibilities:
- Design, develop, and deploy machine learning models and AI solutions
- Build and maintain ML pipelines using Python, TensorFlow, and PyTorch
- Work with large language models (LLMs) and generative AI applications
- Develop REST APIs using FastAPI or Flask to serve ML models
- Collaborate with cross-functional teams to integrate AI into products
- Write clean, well-documented, and testable code

Requirements:
- Bachelor's degree in Computer Science, AI, or related field
- 2+ years of experience in ML/AI engineering
- Strong proficiency in Python
- Experience with TensorFlow, PyTorch, or similar frameworks
- Familiarity with NLP, computer vision, or generative AI
- Experience with Docker and cloud platforms (AWS, GCP, or Azure)
- Good understanding of REST APIs and microservices architecture

Nice to have:
- Experience with LangChain, Ollama, or similar LLM frameworks
- Familiarity with CI/CD pipelines
- Knowledge of Kubernetes

Salary: Competitive, based on experience
Location: Dubai, UAE (on-site)
"""


def main():
    print("\n" + "=" * 60)
    print("  AI ENGINE -- END-TO-END TEST")
    print("=" * 60)

    # ── Step 1: Test Ollama Connectivity ──────────────────────────────────────
    print("\n[STEP 1] Testing Ollama connectivity...")
    client = OllamaClient(
        base_url=SETTINGS.ollama_base_url,
        model=SETTINGS.ollama_model,
    )

    if client.health_check():
        print(f"   [OK] Ollama is running at {SETTINGS.ollama_base_url}")
        print(f"   [OK] Model loaded: {SETTINGS.ollama_model}")
    else:
        print(f"   [FAIL] Cannot connect to Ollama at {SETTINGS.ollama_base_url}")
        print(f"      Make sure Ollama is running: ollama serve")
        print(f"      Make sure model is pulled: ollama pull {SETTINGS.ollama_model}")
        sys.exit(1)

    # ── Step 2: Load Profile ─────────────────────────────────────────────────
    print("\n[STEP 2] Loading candidate profile...")
    try:
        profile = CandidateProfile.load_from_file(SETTINGS.user_profile_path)
        print(f"   [OK] Profile loaded: {profile.personal.full_name}")
        print(f"   Skills: {', '.join(profile.skills[:8])}...")
        print(f"\n   Profile summary for AI:")
        print("   " + "-" * 40)
        for line in profile.to_summary().split("\n"):
            print(f"   {line}")
        print("   " + "-" * 40)
    except Exception as e:
        print(f"   [FAIL] Failed to load profile: {e}")
        sys.exit(1)

    # ── Step 3: Test Job Matching ────────────────────────────────────────────
    print(f"\n[STEP 3] AI Job Matching...")
    print(f"   Sending job '{SAMPLE_JOB_TITLE}' to Qwen for analysis...")
    print(f"   (This may take 30-60 seconds on first run...)\n")

    match_result = match_job(
        client=client,
        job_title=SAMPLE_JOB_TITLE,
        job_description=SAMPLE_JOB_DESCRIPTION,
        profile=profile,
    )

    print(f"   +-------------------------------------------+")
    print(f"   |  MATCH SCORE:    {match_result.match_score:>3}%                    |")
    print(f"   |  RECOMMENDATION: {match_result.recommendation:<22} |")
    print(f"   +-------------------------------------------+")
    print(f"\n   Matched Skills:")
    for s in match_result.matched_skills:
        print(f"      + {s}")
    print(f"\n   Missing Skills:")
    for s in match_result.missing_skills:
        print(f"      - {s}")
    print(f"\n   AI Reasoning:")
    print(f"      {match_result.reasoning}")

    # ── Step 4: Test Application Tailoring ────────────────────────────────────
    print(f"\n[STEP 4] AI Application Tailoring...")
    print(f"   Generating personalized cover letter and answers...")
    print(f"   (This may take 30-60 seconds...)\n")

    tailored = tailor_application(
        client=client,
        job_title=SAMPLE_JOB_TITLE,
        job_description=SAMPLE_JOB_DESCRIPTION,
        profile=profile,
    )

    print(f"   AI-Generated Cover Letter:")
    print("   " + "-" * 50)
    for line in tailored.cover_letter.split("\n"):
        print(f"   {line}")
    print("   " + "-" * 50)

    print(f"\n   Key Selling Points:")
    for kp in tailored.key_points:
        print(f"      * {kp}")

    if tailored.suggested_answers:
        print(f"\n   Pre-Answered Questions:")
        for key, answer in tailored.suggested_answers.items():
            print(f"      Q: {key}")
            print(f"      A: {answer}\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60)
    print(f"\n  Your AI engine is working!")
    print(f"  * Ollama: connected")
    print(f"  * Qwen:   responding")
    print(f"  * Match:  {match_result.match_score}% -- {match_result.recommendation}")
    print(f"  * Tailor: {len(tailored.cover_letter)} char cover letter generated")
    print(f"\n  Next: run 'python auto_apply.py --dry-run --limit 3' to test the full pipeline.\n")


if __name__ == "__main__":
    main()
