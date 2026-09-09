"""
greenhouse_adapter.py — Greenhouse ATS adapter.

Handles boards.greenhouse.io and grnh.se redirect links.
Greenhouse forms are typically single-page with:
  - Personal info fields (name, email, phone, location)
  - Resume upload
  - Cover letter textarea
  - LinkedIn/GitHub URLs
  - EEOC/demographic dropdowns (gender, ethnicity, veteran, disability)
  - Custom screening questions
"""

import os
from datetime import datetime, timezone
from typing import Optional, List

from playwright.sync_api import BrowserContext, Page

from applier.adapters.base_adapter import BaseAdapter, AdapterOutcome
from applier.form_filler import HeuristicFormFiller
from applier.url_resolver import resolve_url
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


class GreenhouseAdapter(BaseAdapter):
    name = "greenhouse"

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
        logs = [f"[greenhouse] Applying to: {job.title} @ {job.company}"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshots_dir, f"job_{job.id}_{timestamp}.png")
        os.makedirs(screenshots_dir, exist_ok=True)

        # Resolve redirect (grnh.se links redirect to boards.greenhouse.io)
        final_url, chain = resolve_url(job.url)
        logs.append(f"Resolved URL: {final_url} (hops: {len(chain)})")

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
            page.wait_for_timeout(2500)
            outcome.redirect_url = page.url

            # Click "Apply for this job" button if present (some Greenhouse boards
            # show a listing page first)
            for btn_text in ["Apply for this job", "Apply Now", "Apply"]:
                try:
                    btn = page.locator(f"a:has-text('{btn_text}'), button:has-text('{btn_text}')").first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        page.wait_for_timeout(2000)
                        logs.append(f"Clicked '{btn_text}' button.")
                        break
                except Exception:
                    pass

            # Greenhouse-specific field fills
            p = profile.personal
            prefs = profile.work_preferences
            sa = profile.screening_answers
            cover = ai_cover_letter or profile.cover_letter_template
            resume_path = os.path.abspath(p.resume_path)

            fields_filled = 0

            # ── Personal info ──────────────────────────────────────────────────
            fields_filled += self._fill_text(page, "#first_name, input[name='job_application[first_name]']", p.first_name, "first_name", logs)
            fields_filled += self._fill_text(page, "#last_name, input[name='job_application[last_name]']", p.last_name, "last_name", logs)
            fields_filled += self._fill_text(page, "#email, input[name='job_application[email]'], input[type='email']", p.email, "email", logs)
            fields_filled += self._fill_text(page, "#phone, input[name='job_application[phone]']", p.phone, "phone", logs)
            fields_filled += self._fill_text(page, "input[name*='location'], input[id*='location']", p.location, "location", logs)
            fields_filled += self._fill_text(page, "input[name*='linkedin'], input[id*='linkedin']", p.linkedin_url, "linkedin", logs)
            fields_filled += self._fill_text(page, "input[name*='github'], input[id*='github']", p.github_url, "github", logs)
            fields_filled += self._fill_text(page, "input[name*='website'], input[name*='portfolio']", p.portfolio_url or p.linkedin_url, "website", logs)

            # ── Resume upload ──────────────────────────────────────────────────
            if os.path.isfile(resume_path):
                for sel in ["input[type='file'][name*='resume'], input[type='file'][id*='resume'], input[type='file']"]:
                    try:
                        file_inp = page.locator(sel).first
                        if file_inp.is_visible(timeout=2000):
                            file_inp.set_input_files(resume_path)
                            fields_filled += 1
                            logs.append(f"Resume attached: {resume_path}")
                            break
                    except Exception:
                        pass

            # ── Cover letter ───────────────────────────────────────────────────
            for sel in [
                "textarea[name*='cover_letter'], textarea[id*='cover'], textarea[name*='cover']",
                "textarea",
            ]:
                try:
                    ta = page.locator(sel).first
                    if ta.is_visible(timeout=2000) and not ta.input_value():
                        ta.fill(cover)
                        fields_filled += 1
                        logs.append("Cover letter filled.")
                        break
                except Exception:
                    pass

            # ── EEOC / demographic selects ─────────────────────────────────────
            for sel_id, keywords, prefer_not_keywords in [
                ("#gender", ["gender"], ["prefer not", "decline"]),
                ("#race", ["race", "ethnicity"], ["prefer not", "decline"]),
                ("#veteran_status", ["veteran"], ["no", "prefer not"]),
                ("#disability_status", ["disability"], ["no", "prefer not"]),
            ]:
                try:
                    sel_el = page.locator(sel_id).first
                    if sel_el.is_visible(timeout=1500):
                        # Select "prefer not to say" or "decline"
                        opts = sel_el.evaluate(
                            "el => Array.from(el.options).map(o => ({v:o.value, t:o.text.toLowerCase()}))"
                        )
                        for opt in opts:
                            if any(k in opt["t"] for k in prefer_not_keywords):
                                sel_el.select_option(value=opt["v"])
                                fields_filled += 1
                                logs.append(f"EEOC {sel_id} → decline/prefer not")
                                break
                except Exception:
                    pass

            # ── Work authorization ─────────────────────────────────────────────
            auth_val = sa.get("authorized_to_work", "yes").lower()
            for sel in ["select[name*='authorized'], select[id*='authorized']"]:
                try:
                    sel_el = page.locator(sel).first
                    if sel_el.is_visible(timeout=1500):
                        opts = sel_el.evaluate(
                            "el => Array.from(el.options).map(o => ({v:o.value, t:o.text.toLowerCase()}))"
                        )
                        want = "yes" if auth_val == "yes" else "no"
                        for opt in opts:
                            if want in opt["t"]:
                                sel_el.select_option(value=opt["v"])
                                fields_filled += 1
                                logs.append(f"Work authorization → {want}")
                                break
                except Exception:
                    pass

            # ── Fall back to generic heuristic for anything missed ─────────────
            filler = HeuristicFormFiller(
                profile=profile,
                dry_run=True,
                ai_cover_letter=ai_cover_letter,
                ai_suggested_answers=ai_answers or {},
                ai_client=ai_client,
            )
            fill_result = filler.fill_form(page)
            logs.extend(fill_result.logs)
            fields_filled += fill_result.fields_filled

            self._screenshot(page, screenshot_path)

            if fields_filled == 0:
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "No fields found on Greenhouse form."
                return outcome

            # ── Submit ────────────────────────────────────────────────────────
            if dry_run:
                logs.append("DRY RUN — not submitting.")
                outcome.status = "DRY_RUN"
            else:
                submitted = self._submit(page, logs)
                outcome.status = "APPLIED" if submitted else "REQUIRES_MANUAL"
                if not submitted:
                    outcome.error_reason = "Could not find/click the Submit button."

        except Exception as e:
            logs.append(f"GreenhouseAdapter error: {e}")
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

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _fill_text(page: Page, selector: str, value: str, label: str, logs: List[str]) -> int:
        if not value:
            return 0
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=2000):
                current = el.input_value()
                if not current:
                    el.fill(value)
                    logs.append(f"  {label}: {value[:60]}")
                    return 1
        except Exception:
            pass
        return 0

    @staticmethod
    def _submit(page: Page, logs: List[str]) -> bool:
        for sel in [
            "input[type='submit']",
            "button[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            "#submit_app",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    page.wait_for_timeout(4000)
                    logs.append(f"Submitted via: {sel}")
                    return True
            except Exception:
                pass
        logs.append("No submit button found.")
        return False
