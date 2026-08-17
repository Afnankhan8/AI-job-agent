"""
Base connector contract.

Every job source (Adzuna, Jooble, an ATS feed, etc.) must implement
this interface and return jobs in the same RawJob shape. This is
what lets the rest of the product stay source-agnostic: if Adzuna
disappears tomorrow, nothing downstream breaks.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class RawJob:
    """
    The common shape every connector must produce, regardless of
    the source's native API format. Fields that a source doesn't
    provide should be left as None rather than guessed.
    """
    source_name: str            # e.g. "adzuna"
    source_job_id: str          # the ID the source uses internally
    title: str
    company: Optional[str]
    location: Optional[str]
    description: Optional[str]
    url: Optional[str]
    salary_min: Optional[float]
    salary_max: Optional[float]
    currency: Optional[str]
    contract_type: Optional[str]     # full_time / part_time / contract / unknown
    created_at_source: Optional[datetime]  # timestamp the SOURCE reports
    raw_payload: dict = field(default_factory=dict)  # original response, for debugging/audit


class JobSourceConnector(ABC):
    """
    Abstract connector. Concrete sources (AdzunaConnector, JoobleConnector, ...)
    implement `fetch_jobs`. Nothing else in the app should import a
    concrete connector directly except the ingestion orchestrator.
    """

    name: str

    @abstractmethod
    def fetch_jobs(self, what: str, where: str, page: int = 1) -> list[RawJob]:
        """
        Fetch one page of jobs matching the search terms.
        Must raise a JobSourceError (not a bare exception) on failure,
        so the ingestion layer can log/retry/skip predictably.
        """
        raise NotImplementedError


class JobSourceError(Exception):
    """Raised when a connector fails to fetch or parse data from its source."""
    def __init__(self, source_name: str, message: str):
        self.source_name = source_name
        self.message = message
        super().__init__(f"[{source_name}] {message}")
