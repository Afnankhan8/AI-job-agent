"""
Deduplication layer.

The same job often appears on multiple sources, or the same source
returns it again on the next poll. We never want the user to see
five copies of "AI Engineer @ Company X". This module decides, given
a NormalizedJob and the existing database, whether this is:
  - a brand-new job -> insert
  - a job we've seen before (same dedup_key) -> update last_seen, don't duplicate
"""

from dataclasses import dataclass
from jobs.normalizer import NormalizedJob


@dataclass
class DedupDecision:
    is_duplicate: bool
    existing_job_id: int | None  # database primary key of the existing row, if duplicate


def check_duplicate(normalized: NormalizedJob, repository) -> DedupDecision:
    """
    repository must expose find_by_dedup_key(dedup_key) -> Optional[JobRecord]
    Kept as a thin function (not a class) since the logic itself is simple;
    complexity belongs in the repository's lookup, not here.
    """
    existing = repository.find_by_dedup_key(normalized.dedup_key)
    if existing is None:
        return DedupDecision(is_duplicate=False, existing_job_id=None)
    return DedupDecision(is_duplicate=True, existing_job_id=existing.id)
