"""
generic_adapter.py — Fallback adapter for unknown/direct application portals.

Wraps the HeuristicFormFiller for any portal not matched by a specific adapter.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from playwright.sync_api import BrowserContext

from applier.adapters.base_adapter import BaseAdapter, AdapterOutcome
from applier.form_filler import HeuristicFormFiller
from applier.url_resolver import resolve_url
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


class GenericAdapter(BaseAdapter):
    name = "generic"

    def apply(
        self,
        job: JobRecord,
        profile: CandidateProfile,
        context: BrowserContext,
        dry_run: bool = False,
        ai_cover_letter: Optional[str] = None,
        ai_answers: Optional[dict] = None,
        screenshots_dir: str = "applications/screenshots",
        ai_client=None,
        **kwargs,
    ) -> AdapterOutcome:
        logs = [f"[generic] Applying to: {job.title} @ {job.company}"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshots_dir, f"job_{job.id}_{timestamp}.png")

        # Resolve URL
        final_url, _ = resolve_url(job.url)
        logs.append(f"Resolved URL: {final_url}")

        outcome = AdapterOutcome(
            status="FAILED",
            redirect_url=final_url,
            screenshot_path=screenshot_path,
            logs=logs,
        )

        page = None
        try:
            page = self._new_page(context)
            page.goto(final_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # Cloudflare wait
            if self._wait_for_cloudflare(page, logs):
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "Blocked by Cloudflare."
                self._screenshot(page, screenshot_path)
                return outcome

            filler = HeuristicFormFiller(
                profile=profile,
                dry_run=dry_run,
                ai_cover_letter=ai_cover_letter,
                ai_suggested_answers=ai_answers or {},
                ai_client=ai_client,
            )
            fill = filler.fill_form(page)
            logs.extend(fill.logs)

            self._screenshot(page, screenshot_path)

            if fill.status == "NO_FORM_FOUND":
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = f"No form detected at {final_url}. Open manually to apply."
            elif fill.status == "MANUAL_REQUIRED":
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = fill.requires_manual_reason
            elif fill.status == "SUCCESS":
                outcome.status = "DRY_RUN" if dry_run else "APPLIED"
            elif fill.status == "PARTIAL":
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "Partial form fill — manual submission needed."
            else:
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = f"Unexpected fill status: {fill.status}"

        except Exception as e:
            logs.append(f"GenericAdapter error: {e}")
            outcome.status = "FAILED"
            outcome.error_reason = str(e)
            if page:
                self._screenshot(page, screenshot_path)
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

        return outcome

    @staticmethod
    def _wait_for_cloudflare(page, logs: list, max_wait_s: int = 80) -> bool:
        """Wait for Cloudflare to resolve (up to max_wait_s seconds). Returns True if timed out."""
        checks = 0
        limit = max_wait_s // 2
        while checks < limit:
            try:
                html = page.content().lower()
            except Exception:
                page.wait_for_timeout(2000)
                checks += 1
                continue

            if any(k in html for k in ["performing security verification", "verify you are human", "cf-browser-verification"]):
                if checks == 0:
                    logs.append("Cloudflare CAPTCHA detected — waiting for manual resolution...")
                    print("\n>>> CLOUDFLARE: Please complete verification in the browser window! <<<\n")
                page.wait_for_timeout(2000)
                checks += 1
            else:
                if checks > 0:
                    logs.append("Cloudflare passed.")
                    page.wait_for_timeout(2000)
                return False  # not timed out — page is OK

        return True  # timed out
