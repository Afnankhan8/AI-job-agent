"""Deterministic job-scope checks used before an application is attempted."""

import re
from dataclasses import dataclass

from config.profile_loader import CandidateProfile
from database.repository import JobRecord


@dataclass(frozen=True)
class ScopeResult:
    allowed: bool
    reason: str


def _terms(value: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 1]


# ── Synonym expansions ──────────────────────────────────────────────────────
# When the user says "AI Engineer", also accept "Artificial Intelligence Engineer" etc.
_TITLE_SYNONYMS: dict[str, list[str]] = {
    "ai": ["ai", "artificial intelligence", "a.i"],
    "ml": ["ml", "machine learning"],
    "nlp": ["nlp", "natural language processing"],
    "cv": ["cv", "computer vision"],
    "data": ["data", "analytics"],
    "devops": ["devops", "dev ops", "platform"],
    "sre": ["sre", "site reliability"],
    "llm": ["llm", "large language model"],
}

# When the user says "UAE", also match "Dubai", "Abu Dhabi", "Sharjah", etc.
_LOCATION_ALIASES: dict[str, list[str]] = {
    "uae": [
        "uae", "united arab emirates", "dubai", "abu dhabi", "sharjah",
        "ajman", "ras al", "fujairah", "umm al",
    ],
    "uk": ["uk", "united kingdom", "london", "england", "scotland", "wales", "manchester", "birmingham"],
    "us": ["us", "usa", "united states", "new york", "california", "texas", "san francisco", "seattle"],
    "ksa": ["ksa", "saudi", "riyadh", "jeddah", "dammam"],
    "india": ["india", "bangalore", "mumbai", "delhi", "hyderabad", "pune", "chennai"],
}


def _expand_title_terms(terms: list[str]) -> list[str]:
    """Expand short terms like 'ai' into their full synonyms."""
    expanded = set()
    for term in terms:
        if term in _TITLE_SYNONYMS:
            expanded.update(_TITLE_SYNONYMS[term])
        else:
            expanded.add(term)
    return list(expanded)


def _expand_location_terms(terms: list[str]) -> list[str]:
    """Expand location shortcodes like 'uae' into cities/regions."""
    expanded = set()
    for term in terms:
        matched = False
        for key, aliases in _LOCATION_ALIASES.items():
            if term == key or term in aliases:
                expanded.update(aliases)
                matched = True
                break
        if not matched:
            expanded.add(term)
    return list(expanded)


def check_job_scope(
    job: JobRecord,
    profile: CandidateProfile,
    target_location: str,
) -> ScopeResult:
    """Apply conservative title/location checks before browser automation."""
    title = job.title.lower()
    desired_terms = _terms(profile.work_preferences.desired_title)
    important_terms = [term for term in desired_terms if term not in {"engineer", "developer", "specialist"}]
    expanded_title_terms = _expand_title_terms(important_terms)
    if important_terms and not any(term in title for term in expanded_title_terms):
        return ScopeResult(False, "title is outside the configured target role")

    target_terms = _terms(target_location)
    expanded_loc_terms = _expand_location_terms(target_terms)
    location = (job.location or "").lower()
    if target_terms and location:
        if not any(term in location for term in expanded_loc_terms) and "remote" not in location:
            return ScopeResult(False, "location is outside the configured target location")

    return ScopeResult(True, "within configured title and location scope")

