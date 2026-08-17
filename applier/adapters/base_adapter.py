"""
base_adapter.py — Abstract base class for all platform adapters.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from playwright.sync_api import BrowserContext, Page

from config.profile_loader import CandidateProfile
from database.repository import JobRecord


@dataclass
class AdapterOutcome:
    status: str                    # APPLIED | DRY_RUN | REQUIRES_MANUAL | FAILED | SKIPPED
    redirect_url: str = ""
    screenshot_path: Optional[str] = None
    error_reason: Optional[str] = None
    logs: List[str] = field(default_factory=list)


class BaseAdapter(ABC):
    """Base class all platform adapters must implement."""

    name: str = "base"

    @abstractmethod
    def apply(
        self,
        job: JobRecord,
        profile: CandidateProfile,
        context: BrowserContext,
        dry_run: bool = False,
        ai_cover_letter: Optional[str] = None,
        ai_answers: Optional[dict] = None,
        screenshots_dir: str = "applications/screenshots",
    ) -> AdapterOutcome:
        """Execute the full application flow for one job."""
        ...

    # ── Shared helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _new_page(context: BrowserContext) -> Page:
        page = context.new_page()
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
        return page

    @staticmethod
    def _screenshot(page: Page, path: str) -> None:
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            page.screenshot(path=path, full_page=True)
        except Exception:
            pass

    @staticmethod
    def _safe_fill(locator, value: str, logs: list, label: str = "") -> bool:
        """Fill a locator safely, returning True on success."""
        try:
            if locator.is_visible(timeout=2000):
                locator.fill(value)
                if label:
                    logs.append(f"Filled {label}: {value[:60]}")
                return True
        except Exception as e:
            if label:
                logs.append(f"Fill failed for {label}: {e}")
        return False

    @staticmethod
    def _safe_click(locator, logs: list, label: str = "") -> bool:
        """Click a locator safely, returning True on success."""
        try:
            if locator.is_visible(timeout=3000):
                locator.click()
                if label:
                    logs.append(f"Clicked: {label}")
                return True
        except Exception as e:
            if label:
                logs.append(f"Click failed for {label}: {e}")
        return False
