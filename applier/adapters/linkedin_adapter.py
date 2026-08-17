"""
linkedin_adapter.py — LinkedIn Easy Apply modal navigation.

Handles the full LinkedIn Easy Apply flow:
  1. Verify logged in (session must already be active via session_manager)
  2. Navigate to job page
  3. Click "Easy Apply" button
  4. Navigate multi-step modal: fill each page, click Next
  5. Handle: text inputs, dropdowns, radio buttons, CV upload
  6. Click Review → Submit (or stop at dry-run)

For jobs with an external "Apply" button (not Easy Apply), resolves the
redirect URL and hands off to the appropriate external adapter.
"""

import os
import re
from datetime import datetime, timezone
from typing import Optional, List

from playwright.sync_api import BrowserContext, Page

from applier.adapters.base_adapter import BaseAdapter, AdapterOutcome
from applier.form_filler import HeuristicFormFiller, _has
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


class LinkedInAdapter(BaseAdapter):
    name = "linkedin"

    # Selectors
    _EASY_APPLY_BTN = (
        "button.jobs-apply-button:has-text('Easy Apply'), "
        "button[aria-label*='Easy Apply'], "
        "button:has-text('Easy Apply')"
    )
    _EXTERNAL_APPLY_BTN = (
        "button.jobs-apply-button:not(:has-text('Easy Apply')), "
        "a.jobs-apply-button"
    )
    _MODAL = ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal']"
    _NEXT_BTN = (
        "button:has-text('Next'), "
        "button[aria-label='Continue to next step'], "
        "footer button.artdeco-button--primary"
    )
    _REVIEW_BTN = (
        "button:has-text('Review'), "
        "button[aria-label='Review your application']"
    )
    _SUBMIT_BTN = (
        "button:has-text('Submit application'), "
        "button[aria-label='Submit application']"
    )
    _DISMISS_BTN = (
        "button[aria-label='Dismiss'], "
        "button.artdeco-modal__dismiss"
    )

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
    ) -> AdapterOutcome:
        logs = [f"[linkedin] Applying to: {job.title} @ {job.company}"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshots_dir, f"job_{job.id}_{timestamp}.png")
        os.makedirs(screenshots_dir, exist_ok=True)

        outcome = AdapterOutcome(
            status="FAILED",
            redirect_url=job.url or "",
            screenshot_path=screenshot_path,
            logs=logs,
        )

        page = None
        try:
            page = self._new_page(context)

            # ── 1. Navigate to job page ────────────────────────────────────────
            job_url = job.url or ""
            logs.append(f"Navigating to: {job_url}")
            page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # ── 2. Check if still logged in ────────────────────────────────────
            if "/authwall" in page.url or "/login" in page.url or "/signup" in page.url:
                logs.append("Auth wall detected — session may have expired.")
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "LinkedIn session expired. Run --login-only to refresh."
                self._screenshot(page, screenshot_path)
                return outcome

            outcome.redirect_url = page.url

            # ── 3. Dismiss any overlay modals ───────────────────────────────────
            self._dismiss_modals(page, logs)

            # ── 4. Find and categorise the Apply button ─────────────────────────
            easy_apply_visible = self._is_visible(page, self._EASY_APPLY_BTN)
            external_visible   = self._is_visible(page, self._EXTERNAL_APPLY_BTN) if not easy_apply_visible else False

            if easy_apply_visible:
                logs.append("Easy Apply button found — starting Easy Apply flow.")
                result = self._do_easy_apply(
                    page, profile, context, dry_run,
                    ai_cover_letter, ai_answers, ai_client, logs, screenshot_path,
                )
                outcome.status = result
                if result == "REQUIRES_MANUAL":
                    outcome.error_reason = "Easy Apply flow could not complete automatically."
            elif external_visible:
                # External redirect — capture where it goes and report as manual
                logs.append("External apply button found — capturing redirect URL.")
                with context.expect_page(timeout=6000) as new_page_info:
                    page.locator(self._EXTERNAL_APPLY_BTN).first.click()
                try:
                    ext_page = new_page_info.value
                    ext_page.wait_for_load_state("domcontentloaded", timeout=15000)
                    outcome.redirect_url = ext_page.url
                    logs.append(f"External URL: {ext_page.url}")
                    ext_page.close()
                except Exception:
                    pass
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = f"External application page. Visit: {outcome.redirect_url}"
            else:
                logs.append("No Apply button found on page.")
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "No Apply / Easy Apply button visible."

            self._screenshot(page, screenshot_path)

        except Exception as e:
            logs.append(f"LinkedInAdapter error: {e}")
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

    # ── Easy Apply multi-step flow ─────────────────────────────────────────────

    def _do_easy_apply(
        self,
        page: Page,
        profile: CandidateProfile,
        context: BrowserContext,
        dry_run: bool,
        ai_cover_letter: Optional[str],
        ai_answers: Optional[dict],
        ai_client,
        logs: List[str],
        screenshot_path: str,
    ) -> str:
        """
        Navigate the Easy Apply modal step by step.
        Returns status string: APPLIED | DRY_RUN | REQUIRES_MANUAL
        """
        # Click Easy Apply
        page.locator(self._EASY_APPLY_BTN).first.click()
        page.wait_for_timeout(2500)

        # Wait for modal
        try:
            page.wait_for_selector(self._MODAL, timeout=10000)
            logs.append("Easy Apply modal opened.")
        except Exception:
            logs.append("Easy Apply modal did not open.")
            return "REQUIRES_MANUAL"

        filler = HeuristicFormFiller(
            profile=profile,
            dry_run=True,              # never auto-submit within the filler — we manage submission here
            ai_cover_letter=ai_cover_letter,
            ai_suggested_answers=ai_answers or {},
            ai_client=ai_client,
        )

        resume_path = os.path.abspath(profile.personal.resume_path)
        resume_attached = os.path.isfile(resume_path)

        max_steps = 12
        for step in range(max_steps):
            logs.append(f"Easy Apply step {step + 1}/{max_steps}")
            page.wait_for_timeout(1500)

            # Fill all visible inputs on this step
            self._fill_easy_apply_step(page, profile, filler, ai_cover_letter, resume_path, resume_attached, logs)

            # Check which buttons are available
            has_next    = self._is_visible(page, self._NEXT_BTN)
            has_review  = self._is_visible(page, self._REVIEW_BTN)
            has_submit  = self._is_visible(page, self._SUBMIT_BTN)

            if has_submit:
                logs.append("Reached Submit step.")
                if dry_run:
                    logs.append("DRY RUN — not clicking Submit.")
                    return "DRY_RUN"
                # Final submit
                page.locator(self._SUBMIT_BTN).first.click()
                page.wait_for_timeout(4000)
                logs.append("Application submitted via Easy Apply!")
                # Dismiss post-submit modal
                self._dismiss_modals(page, logs)
                return "APPLIED"

            elif has_review:
                logs.append("Clicking Review...")
                page.locator(self._REVIEW_BTN).first.click()
                page.wait_for_timeout(2000)

            elif has_next:
                logs.append("Clicking Next...")
                page.locator(self._NEXT_BTN).first.click()
                page.wait_for_timeout(2000)

            else:
                logs.append("No Next/Review/Submit button found — may be stuck.")
                break

        logs.append("Reached max steps without submitting.")
        return "REQUIRES_MANUAL"

    def _fill_easy_apply_step(
        self,
        page: Page,
        profile: CandidateProfile,
        filler: HeuristicFormFiller,
        ai_cover_letter: Optional[str],
        resume_path: str,
        resume_attached: bool,
        logs: List[str],
    ) -> None:
        """Fill all visible fields on the current Easy Apply modal step."""
        p = profile.personal
        prefs = profile.work_preferences
        sa = profile.screening_answers
        cover_letter = ai_cover_letter or profile.cover_letter_template

        try:
            modal = page.locator(self._MODAL).first

            # Text inputs
            for inp in modal.locator("input:visible, textarea:visible").all():
                try:
                    itype = (inp.get_attribute("type") or "text").lower()
                    if itype in ("hidden", "submit", "button", "image", "reset"):
                        continue

                    # File upload
                    if itype == "file":
                        if os.path.isfile(resume_path):
                            inp.set_input_files(resume_path)
                            logs.append("CV uploaded in Easy Apply.")
                        continue

                    current = inp.input_value() if itype != "file" else ""
                    meta = self._elem_meta(page, inp)

                    if current:
                        continue  # already filled

                    # Map meta to profile values
                    if "email" in meta:
                        inp.fill(p.email)
                    elif any(k in meta for k in ["first name", "firstname", "given"]):
                        inp.fill(p.first_name)
                    elif any(k in meta for k in ["last name", "lastname", "surname", "family"]):
                        inp.fill(p.last_name)
                    elif any(k in meta for k in ["full name", "fullname", "your name"]):
                        inp.fill(p.full_name)
                    elif any(k in meta for k in ["phone", "mobile", "tel"]):
                        inp.fill(p.phone)
                    elif "linkedin" in meta:
                        inp.fill(p.linkedin_url)
                    elif "github" in meta:
                        inp.fill(p.github_url)
                    elif any(k in meta for k in ["city", "location"]):
                        inp.fill(p.location)
                    elif any(k in meta for k in ["cover letter", "coverletter", "motivation"]):
                        inp.fill(cover_letter)
                    elif any(k in meta for k in ["salary", "compensation"]):
                        inp.fill(prefs.expected_salary)
                    elif inp.evaluate("el => el.tagName.toLowerCase()") == "textarea":
                        # Unknown textarea — use cover letter
                        inp.fill(cover_letter)
                    logs.append(f"Filled field: '{meta[:50]}'")
                except Exception as e:
                    logs.append(f"Easy Apply field error: {e}")

            # Select dropdowns
            for sel in modal.locator("select:visible").all():
                try:
                    meta = self._elem_meta(page, sel)
                    options = sel.evaluate(
                        "el => Array.from(el.options).map(o => ({v: o.value, t: o.text.toLowerCase()}))"
                    )
                    chosen = None

                    if any(k in meta for k in ["experience", "years"]):
                        yoe = prefs.years_of_experience
                        for opt in options:
                            nums = re.findall(r"\d+", opt["t"])
                            if nums and int(nums[0]) >= yoe:
                                chosen = opt["v"]
                                break
                        if not chosen and options:
                            chosen = options[-1]["v"]

                    elif any(k in meta for k in ["authorized", "eligible", "right to work"]):
                        want = "yes" if sa.get("authorized_to_work", "yes").lower() == "yes" else "no"
                        for opt in options:
                            if want in opt["t"]:
                                chosen = opt["v"]
                                break

                    elif any(k in meta for k in ["sponsor", "sponsorship"]):
                        want = "no"
                        for opt in options:
                            if want in opt["t"] or "not required" in opt["t"]:
                                chosen = opt["v"]
                                break

                    elif any(k in meta for k in ["notice", "available"]):
                        for opt in options:
                            if "immediately" in opt["t"] or "0" in opt["t"] or "2 weeks" in opt["t"]:
                                chosen = opt["v"]
                                break

                    if chosen:
                        sel.select_option(value=chosen)
                        logs.append(f"Selected '{meta[:40]}' → '{chosen}'")
                except Exception as e:
                    logs.append(f"Easy Apply select error: {e}")

            # Radio buttons — Yes/No questions
            radio_groups: dict = {}
            for radio in modal.locator("input[type='radio']:visible").all():
                name = radio.get_attribute("name") or ""
                if name:
                    radio_groups.setdefault(name, []).append(radio)

            for name, group in radio_groups.items():
                meta = self._elem_meta(page, group[0])
                want = None
                if any(k in meta for k in ["authorized", "eligible", "right to work"]):
                    want = "yes" if sa.get("authorized_to_work", "yes").lower() == "yes" else "no"
                elif any(k in meta for k in ["sponsor", "sponsorship"]):
                    want = "no" if sa.get("require_sponsorship", "no").lower() == "no" else "yes"
                elif any(k in meta for k in ["relocate", "relocation"]):
                    want = "yes" if sa.get("willing_to_relocate", "yes").lower() == "yes" else "no"
                if want:
                    for radio in group:
                        val = (radio.get_attribute("value") or "").lower()
                        lbl = self._elem_meta(page, radio)
                        if want in val or want in lbl:
                            radio.check()
                            logs.append(f"Radio '{name}' → '{want}'")
                            break

        except Exception as e:
            logs.append(f"Step fill error: {e}")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _dismiss_modals(self, page: Page, logs: List[str]) -> None:
        for sel in [
            "button[aria-label='Dismiss']",
            "button.artdeco-modal__dismiss",
            "button.modal__dismiss",
            "button[data-test-modal-close-btn]",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    logs.append(f"Dismissed modal: {sel}")
                    page.wait_for_timeout(800)
            except Exception:
                pass

    @staticmethod
    def _is_visible(page: Page, selector: str, timeout: int = 3000) -> bool:
        try:
            return page.locator(selector).first.is_visible(timeout=timeout)
        except Exception:
            return False

    @staticmethod
    def _elem_meta(page: Page, elem) -> str:
        try:
            attrs = page.evaluate(
                """el => {
                    const id = el.id || '';
                    const name = el.name || el.getAttribute('name') || '';
                    const ph = el.placeholder || el.getAttribute('aria-label') || '';
                    const lbl = id ? (document.querySelector('label[for="' + id + '"]') || {innerText:''}).innerText : '';
                    return [id, name, ph, lbl].join(' ').toLowerCase();
                }""",
                elem.element_handle() if hasattr(elem, "element_handle") else elem,
            )
            return attrs or ""
        except Exception:
            return ""
