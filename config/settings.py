"""
Central configuration. Loads secrets from .env and exposes
typed settings to the rest of the application.

Never hardcode API keys anywhere else in the codebase.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    adzuna_app_id: Optional[str]
    adzuna_app_key: Optional[str]
    jooble_api_key: Optional[str]
    database_path: str
    default_country: str        # Adzuna country code (gb, us, au, etc.)
    default_what: str           # Search keywords
    default_where: str          # Location
    results_per_page: int
    user_profile_path: str
    screenshots_dir: str
    default_headless: bool
    auto_apply_limit: int
    ollama_base_url: str
    ollama_model: str
    ai_match_threshold: int


def load_settings() -> Settings:
    adzuna_id = os.getenv("ADZUNA_APP_ID")
    adzuna_key = os.getenv("ADZUNA_APP_KEY")
    jooble_key = os.getenv("JOOBLE_API_KEY")

    if not any([adzuna_id and adzuna_key, jooble_key]):
        raise ValueError(
            "No job source credentials found in .env. "
            "Provide at least ADZUNA_APP_ID + ADZUNA_APP_KEY, or JOOBLE_API_KEY."
        )

    return Settings(
        adzuna_app_id=adzuna_id,
        adzuna_app_key=adzuna_key,
        jooble_api_key=jooble_key,
        database_path=os.getenv("DATABASE_PATH", "jobs.db"),
        default_country=os.getenv("DEFAULT_COUNTRY", "gb"),
        default_what=os.getenv("DEFAULT_WHAT", "AI Engineer"),
        default_where=os.getenv("DEFAULT_WHERE", "UAE"),
        results_per_page=int(os.getenv("RESULTS_PER_PAGE", "20")),
        user_profile_path=os.getenv("USER_PROFILE_PATH", "config/user_profile.json"),
        screenshots_dir=os.getenv("SCREENSHOTS_DIR", "applications/screenshots"),
        default_headless=os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes"),
        auto_apply_limit=int(os.getenv("AUTO_APPLY_LIMIT", "50")),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3-coder:30b"),
        ai_match_threshold=int(os.getenv("AI_MATCH_THRESHOLD", "50")),
    )


SETTINGS = load_settings()
