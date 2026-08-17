"""
applier/adapters/__init__.py — Platform detection and adapter registry.

Given a resolved job application URL, detects which ATS platform is being
used and returns the correct adapter to apply through it.

Supported platforms:
  linkedin    — LinkedIn Easy Apply (in-page modal)
  greenhouse  — Greenhouse (boards.greenhouse.io / grnh.se)
  lever       — Lever (jobs.lever.co)
  workday     — Workday (myworkdayjobs.com / wd*.myworkday.com)
  ashby       — Ashby (jobs.ashbyhq.com)
  generic     — Any other direct company application page
"""

from __future__ import annotations
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from applier.adapters.base_adapter import BaseAdapter


# ── Platform detection ─────────────────────────────────────────────────────────

_PLATFORM_PATTERNS = [
    ("linkedin",   r"linkedin\.com/jobs"),
    ("greenhouse", r"greenhouse\.io|grnh\.se|boards\.greenhouse"),
    ("lever",      r"jobs\.lever\.co|lever\.co/"),
    ("workday",    r"myworkdayjobs\.com|myworkday\.com|wd\d+\.myworkdayjobs"),
    ("ashby",      r"jobs\.ashbyhq\.com|ashbyhq\.com"),
    ("smartrec",   r"smartrecruiters\.com|jobs\.smartrecruiters"),
    ("bamboo",     r"bamboohr\.com/jobs|app\.bamboohr"),
    ("icims",      r"icims\.com"),
]


def detect_platform(url: str) -> str:
    """
    Return the platform name for a given URL.
    Falls back to 'generic' for unknown portals.
    """
    url_lower = (url or "").lower()
    for name, pattern in _PLATFORM_PATTERNS:
        if re.search(pattern, url_lower):
            return name
    return "generic"


def get_adapter(platform: str) -> "BaseAdapter":
    """
    Return the correct adapter instance for the given platform name.
    """
    from applier.adapters.linkedin_adapter   import LinkedInAdapter
    from applier.adapters.greenhouse_adapter import GreenhouseAdapter
    from applier.adapters.lever_adapter      import LeverAdapter
    from applier.adapters.workday_adapter    import WorkdayAdapter
    from applier.adapters.generic_adapter    import GenericAdapter

    _REGISTRY = {
        "linkedin":   LinkedInAdapter,
        "greenhouse": GreenhouseAdapter,
        "lever":      LeverAdapter,
        "workday":    WorkdayAdapter,
        "ashby":      GreenhouseAdapter,   # Ashby is structurally similar to Greenhouse
        "smartrec":   GenericAdapter,
        "bamboo":     GenericAdapter,
        "icims":      GenericAdapter,
        "generic":    GenericAdapter,
    }

    cls = _REGISTRY.get(platform, GenericAdapter)
    return cls()
