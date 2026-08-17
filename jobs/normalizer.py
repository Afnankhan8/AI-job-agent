"""
Normalization layer.

Different sources describe the same job differently
("AI Engineer" vs "Artificial Intelligence Engineer" vs "GenAI Engineer").
This module doesn't try to be a full NLP title-matcher yet — that's
a later milestone (semantic matching). For now it does deterministic,
honest cleanup: trimming, casing, contract-type mapping, and building
a stable normalized_key used for deduplication.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from job_sources.base import RawJob

CONTRACT_TYPE_MAP = {
    "full_time": "full_time",
    "part_time": "part_time",
    "contract": "contract",
    "permanent": "full_time",
    "temporary": "contract",
}


@dataclass
class NormalizedJob:
    source_name: str
    source_job_id: str
    title: str
    title_normalized: str
    company: Optional[str]
    company_normalized: Optional[str]
    location: Optional[str]
    description: Optional[str]
    url: Optional[str]
    salary_min: Optional[float]
    salary_max: Optional[float]
    currency: Optional[str]
    contract_type: str
    created_at_source: Optional[datetime]
    dedup_key: str  # used by the deduplicator to find likely-duplicate postings


def _clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _normalize_title(title: str) -> str:
    title = _clean_text(title).lower()
    # Deterministic synonym folding — expand this table over time
    # rather than relying on guesswork.
    replacements = {
        "artificial intelligence engineer": "ai engineer",
        "genai engineer": "ai engineer",
        "generative ai engineer": "ai engineer",
        "machine learning engineer": "ml engineer",
    }
    return replacements.get(title, title)


def _normalize_company(company: Optional[str]) -> Optional[str]:
    if not company:
        return None
    company = _clean_text(company).lower()
    company = re.sub(r"\b(llc|fze|fzc|ltd|inc|co\.?)\b", "", company).strip()
    return company or None


def _build_dedup_key(title_normalized: str, company_normalized: Optional[str], location: Optional[str]) -> str:
    location_clean = _clean_text(location).lower()
    raw_key = f"{title_normalized}|{company_normalized or ''}|{location_clean}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def normalize_job(raw: RawJob) -> NormalizedJob:
    title_clean = _clean_text(raw.title)
    title_normalized = _normalize_title(title_clean)
    company_clean = _clean_text(raw.company) if raw.company else None
    company_normalized = _normalize_company(raw.company)
    location_clean = _clean_text(raw.location) if raw.location else None

    contract_type = CONTRACT_TYPE_MAP.get(
        (raw.contract_type or "").lower(), "unknown"
    )

    dedup_key = _build_dedup_key(title_normalized, company_normalized, location_clean)

    return NormalizedJob(
        source_name=raw.source_name,
        source_job_id=raw.source_job_id,
        title=title_clean,
        title_normalized=title_normalized,
        company=company_clean,
        company_normalized=company_normalized,
        location=location_clean,
        description=_clean_text(raw.description) if raw.description else None,
        url=raw.url,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        currency=raw.currency,
        contract_type=contract_type,
        created_at_source=raw.created_at_source,
        dedup_key=dedup_key,
    )
