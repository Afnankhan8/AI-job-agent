"""
Freshness engine.

We do NOT trust a source's "posted 2 days ago" label at face value —
sources reformat, cache, or backdate timestamps. Instead we track,
per job, when WE first and last saw it, and derive a freshness label
from the source's reported creation time when available, falling
back to our own first-seen time.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class FreshnessLabel(str, Enum):
    TODAY = "TODAY"
    NEW = "NEW"          # within last 24h
    RECENT = "RECENT"    # 1-3 days
    OLD = "OLD"           # older than 3 days
    UNKNOWN = "UNKNOWN"   # no reliable timestamp at all


@dataclass
class FreshnessResult:
    label: FreshnessLabel
    age_hours: Optional[float]
    basis: str  # "source_timestamp" or "first_seen_fallback"


def classify_freshness(
    created_at_source: Optional[datetime],
    first_seen_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> FreshnessResult:
    now = now or datetime.now(timezone.utc)

    reference = created_at_source
    basis = "source_timestamp"

    if reference is None:
        reference = first_seen_at
        basis = "first_seen_fallback"

    if reference is None:
        return FreshnessResult(label=FreshnessLabel.UNKNOWN, age_hours=None, basis="none")

    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    age = now - reference
    age_hours = age.total_seconds() / 3600

    if age_hours < 0:
        # Clock skew or bad source data — don't claim it's from the future.
        age_hours = 0

    if age_hours <= 24 and reference.date() == now.date():
        label = FreshnessLabel.TODAY
    elif age_hours <= 24:
        label = FreshnessLabel.NEW
    elif age_hours <= 72:
        label = FreshnessLabel.RECENT
    else:
        label = FreshnessLabel.OLD

    return FreshnessResult(label=label, age_hours=round(age_hours, 1), basis=basis)
