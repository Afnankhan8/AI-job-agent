"""
lever_adapter.py — Lever ATS adapter.

Handles jobs.lever.co application pages.
Lever forms are clean single-page forms with:
  - Name (full), Email, Phone
  - LinkedIn, GitHub, Portfolio URLs
  - Resume file upload
  - Cover letter textarea
  - "Why do you want to work here?" and similar questions
  - Submit button
"""

import os
from datetime import datetime, timezone
from typing import Optional, List

from playwright.sync_api import BrowserContext, Page

from applier.adapters.base_adapter import BaseAdapter, AdapterOutcome
from applier.url_resolver import resolve_url
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


class LeverAdapter(BaseAdapter):
    name = "lever"

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
        logs = [f"[lever] Applying to: {job.title} @ {job.company}"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshots_dir, f"job_{job.id}_{timestamp}.png")
        os.makedirs(screenshots_dir, exist_ok=True)

        final_url, _ = resolve_url(job.url)
        # For Lever, the application form is at the job URL + "/apply"
        apply_url = final_url.rstrip("/")
        if not apply_url.endswith("/apply"):
            apply_url += "/apply"
        logs.append(f"Apply URL: {apply_url}")

        outcome = AdapterOutcome(
            status="FAILED",
            redirect_url=apply_url,
            screenshot_path=screenshot_path,
            logs=logs,
        )

        page = None
        try:
            page = self._new_page(context)
            page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            outcome.redirect_url = page.url

            p = profile.personal
            prefs = profile.work_preferences
            cover = ai_cover_letter or profile.cover_letter_template
            resume_path = os.path.abspath(p.resume_path)
            fields_filled = 0

            # ── Personal info ──────────────────────────────────────────────────
            fields_filled += self._ff(page, "input[name='name'], #name", p.full_name, "name", logs)
            fields_filled += self._ff(page, "input[name='email'], #email, input[type='email']", p.email, "email", logs)
            fields_filled += self._ff(page, "input[name='phone'], #phone", p.phone, "phone", logs)
            fields_filled += self._ff(page, "input[name='org'], input[name='current-company']", p.location, "org/location", logs)

            # ── URLs ───────────────────────────────────────────────────────────
            fields_filled += self._ff(page, "input[name='urls[LinkedIn]'], input[placeholder*='LinkedIn']", p.linkedin_url, "linkedin", logs)
            fields_filled += self._ff(page, "input[name='urls[GitHub]'], input[placeholder*='GitHub']", p.github_url, "github", logs)
            fields_filled += self._ff(page, "input[name='urls[Portfolio]'], input[placeholder*='portfolio']", p.portfolio_url or p.linkedin_url, "portfolio", logs)
            fields_filled += self._ff(page, "input[name='urls[Other]']", p.linkedin_url, "other_url", logs)

            # ── Resume ─────────────────────────────────────────────────────────
            if os.path.isfile(resume_path):
                for sel in ["input[type='file']"]:
                    try:
                        fi = page.locator(sel).first
                        if fi.is_visible(timeout=2000):
                            fi.set_input_files(resume_path)
                            fields_filled += 1
                            logs.append(f"Resume attached: {resume_path}")
                            break
                    except Exception:
                        pass

            # ── Cover letter + screening questions ─────────────────────────────
            for ta in page.locator("textarea").all():
                try:
                    if not ta.is_visible(timeout=1000):
                        continue
                    current = ta.input_value()
                    if current:
                        continue
                    name = ta.get_attribute("name") or ""
                    placeholder = (ta.get_attribute("placeholder") or "").lower()
                    meta = f"{name} {placeholder}".lower()

                    if any(k in meta for k in ["cover", "motivation", "letter"]):
                        ta.fill(cover)
                        fields_filled += 1
                        logs.append("Cover letter filled.")
                    elif any(k in meta for k in ["why", "interest", "experience", "tell us"]):
                        # Try AI answer first
                        answer = self._ai_answer(meta, profile, ai_answers, ai_client)
                        ta.fill(answer or cover)
                        fields_filled += 1
                        logs.append(f"Textarea '{meta[:50]}' filled.")
                    else:
                        ta.fill(cover)
                        fields_filled += 1
                        logs.append(f"Unknown textarea '{meta[:50]}' filled with cover letter.")
                except Exception as e:
                    logs.append(f"Textarea error: {e}")

            self._screenshot(page, screenshot_path)

            if fields_filled == 0:
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "No fields found on Lever form."
                return outcome

            # ── Submit ─────────────────────────────────────────────────────────
            if dry_run:
                logs.append("DRY RUN — not submitting.")
                outcome.status = "DRY_RUN"
            else:
                submitted = self._submit(page, logs)
                outcome.status = "APPLIED" if submitted else "REQUIRES_MANUAL"
                if not submitted:
                    outcome.error_reason = "Could not submit Lever application."

        except Exception as e:
            logs.append(f"LeverAdapter error: {e}")
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
    def _ff(page: Page, selector: str, value: str, label: str, logs: List[str]) -> int:
        """Fill a text input, return 1 if filled."""
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
    def _ai_answer(meta: str, profile: CandidateProfile, ai_answers: dict, ai_client) -> Optional[str]:
        """Return AI-generated answer for a screening question."""
        if ai_answers:
            for key, keywords in {
                "why_interested":      ["why", "interest", "motivation"],
                "relevant_experience": ["experience", "background"],
                "greatest_strength":   ["strength", "what makes"],
            }.items():
                if any(k in meta for k in keywords):
                    ans = ai_answers.get(key)
                    if ans:
                        return ans
        if ai_client:
            try:
                prompt = (
                    f"Answer this job application question in 2-3 professional sentences:\n"
                    f"'{meta}'\n\nCandidate: {profile.to_summary()}\n\nAnswer only:"
                )
                return ai_client.generate(prompt, temperature=0.5).strip()
            except Exception:
                pass
        return None

    @staticmethod
    def _submit(page: Page, logs: List[str]) -> bool:
        for sel in [
            "button[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            ".lever-submit",
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
