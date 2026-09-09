"""
workday_adapter.py — Workday ATS adapter.

Handles myworkdayjobs.com and wd*.myworkday.com application portals.
Workday is heavily iframe-based and requires:
  1. Navigating to the job posting
  2. Clicking "Apply" (may open a new tab or redirect)
  3. Creating or signing into a Workday account (or using guest apply)
  4. Filling multi-step form through iframes
  5. Uploading resume and answering screening questions
  6. Submitting

Note: Workday portals vary significantly by company. This adapter handles
common patterns but marks as REQUIRES_MANUAL for complex authentication flows.
"""

import os
from datetime import datetime, timezone
from typing import Optional, List

from playwright.sync_api import BrowserContext, Page

from applier.adapters.base_adapter import BaseAdapter, AdapterOutcome
from applier.url_resolver import resolve_url
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


class WorkdayAdapter(BaseAdapter):
    name = "workday"

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
        logs = [f"[workday] Applying to: {job.title} @ {job.company}"]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(screenshots_dir, f"job_{job.id}_{timestamp}.png")
        os.makedirs(screenshots_dir, exist_ok=True)

        final_url, _ = resolve_url(job.url)
        logs.append(f"Resolved URL: {final_url}")

        outcome = AdapterOutcome(
            status="REQUIRES_MANUAL",
            redirect_url=final_url,
            screenshot_path=screenshot_path,
            logs=logs,
        )

        page = None
        try:
            page = self._new_page(context)
            page.goto(final_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            outcome.redirect_url = page.url

            p = profile.personal
            cover = ai_cover_letter or profile.cover_letter_template
            resume_path = os.path.abspath(p.resume_path)

            # ── Find and click Apply button ────────────────────────────────────
            apply_clicked = False
            for sel in [
                "a[data-automation-id='applyButton']",
                "button[data-automation-id='applyButton']",
                "a:has-text('Apply')",
                "button:has-text('Apply')",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        page.wait_for_timeout(3000)
                        apply_clicked = True
                        logs.append(f"Clicked Apply: {sel}")
                        break
                except Exception:
                    pass

            if not apply_clicked:
                logs.append("No Apply button found.")
                outcome.error_reason = f"No Apply button on Workday page. Visit manually: {final_url}"
                self._screenshot(page, screenshot_path)
                return outcome

            # ── Handle guest apply or account creation ─────────────────────────
            # Workday portals vary widely — try every known "Apply Without Account" selector
            guest_selectors = [
                "button:has-text('Apply Without an Account')",
                "button:has-text('Apply without an account')",
                "button:has-text('Apply as Guest')",
                "button:has-text('Apply as a Guest')",
                "a:has-text('Apply Without')",
                "a:has-text('Apply without')",
                "button:has-text('Continue as Guest')",
                "button:has-text('Guest')",
                "[data-automation-id='continueWithoutAccount']",
                "[data-automation-id='guestSignIn']",
                "button:has-text('Continue without creating')",
                "a:has-text('Continue without')",
                "button:has-text('Skip sign in')",
                "button:has-text('Skip Sign In')",
            ]
            guest_clicked = False
            for guest_sel in guest_selectors:
                try:
                    btn = page.locator(guest_sel).first
                    if btn.is_visible(timeout=1500):
                        btn.click()
                        page.wait_for_timeout(2000)
                        logs.append(f"Guest apply selected: {guest_sel}")
                        guest_clicked = True
                        break
                except Exception:
                    pass

            # Check if it wants account sign-in (and guest apply wasn't available)
            needs_login = False
            for sel in ["input[data-automation-id='email']", "input[type='email']"]:
                try:
                    if page.locator(sel).first.is_visible(timeout=2000):
                        needs_login = True
                        break
                except Exception:
                    pass

            if needs_login and p.workday_email:
                logs.append("Workday account sign-in detected — attempting...")
                try:
                    page.fill("input[data-automation-id='email'], input[type='email']", p.workday_email)
                    page.wait_for_timeout(500)
                    if p.workday_password:
                        page.fill("input[type='password']", p.workday_password)
                        page.wait_for_timeout(500)
                        page.click("button[data-automation-id='signInButton'], button[type='submit']")
                        page.wait_for_timeout(3000)
                        logs.append("Workday sign-in submitted.")
                    else:
                        logs.append("No Workday password set — marking REQUIRES_MANUAL.")
                        outcome.error_reason = (
                            "⚠️  Workday account required but workday_password is empty in "
                            "config/user_profile.json. "
                            "Add your Workday password there and retry. "
                            f"Or apply manually at: {page.url}"
                        )
                        self._screenshot(page, screenshot_path)
                        return outcome
                except Exception as e:
                    logs.append(f"Workday sign-in error: {e}")


            # ── Fill form in all frames ─────────────────────────────────────────
            page.wait_for_timeout(3000)
            fields_filled = 0

            for target in [page, *page.frames]:
                try:
                    # Text inputs
                    for inp in target.query_selector_all("input[type='text'], input[type='email'], input[type='tel'], textarea"):
                        try:
                            if not inp.is_visible():
                                continue
                            itype = (inp.get_attribute("type") or "text").lower()
                            automation_id = (inp.get_attribute("data-automation-id") or "").lower()
                            placeholder = (inp.get_attribute("placeholder") or "").lower()
                            meta = f"{automation_id} {placeholder}".lower()

                            current = inp.input_value() if itype != "file" else ""
                            if current:
                                continue

                            if "email" in meta or itype == "email":
                                inp.fill(p.email); fields_filled += 1
                            elif "firstname" in meta or "first name" in placeholder.lower():
                                inp.fill(p.first_name); fields_filled += 1
                            elif "lastname" in meta or "last name" in placeholder.lower():
                                inp.fill(p.last_name); fields_filled += 1
                            elif "phone" in meta or "mobile" in meta:
                                inp.fill(p.phone); fields_filled += 1
                            elif "address" in meta or "city" in meta:
                                inp.fill(p.location); fields_filled += 1
                            elif "linkedin" in meta:
                                inp.fill(p.linkedin_url); fields_filled += 1
                            elif inp.evaluate("el => el.tagName.toLowerCase()") == "textarea":
                                inp.fill(cover); fields_filled += 1
                        except Exception:
                            pass

                    # File inputs
                    for fi in target.query_selector_all("input[type='file']"):
                        try:
                            if fi.is_visible() and os.path.isfile(resume_path):
                                fi.set_input_files(resume_path)
                                fields_filled += 1
                                logs.append("Resume uploaded to Workday.")
                        except Exception:
                            pass

                except Exception as e:
                    logs.append(f"Workday frame error: {e}")

            logs.append(f"Filled {fields_filled} fields in Workday form.")
            self._screenshot(page, screenshot_path)

            if fields_filled == 0:
                outcome.error_reason = f"Could not fill Workday form. Visit manually: {page.url}"
                return outcome

            # ── Next / Submit steps ────────────────────────────────────────────
            max_steps = 8
            for step in range(max_steps):
                page.wait_for_timeout(1500)
                # Look for Submit
                for submit_sel in [
                    "button[data-automation-id='submitButton']",
                    "button:has-text('Submit')",
                ]:
                    try:
                        btn = page.locator(submit_sel).first
                        if btn.is_visible(timeout=2000):
                            if dry_run:
                                logs.append("DRY RUN — not submitting.")
                                outcome.status = "DRY_RUN"
                                return outcome
                            btn.click()
                            page.wait_for_timeout(4000)
                            logs.append("Workday application submitted!")
                            outcome.status = "APPLIED"
                            return outcome
                    except Exception:
                        pass

                # Look for Next
                next_clicked = False
                for next_sel in [
                    "button[data-automation-id='nextButton']",
                    "button:has-text('Next')",
                    "button:has-text('Continue')",
                ]:
                    try:
                        btn = page.locator(next_sel).first
                        if btn.is_visible(timeout=2000):
                            btn.click()
                            page.wait_for_timeout(2000)
                            next_clicked = True
                            logs.append(f"Workday Next clicked (step {step+1})")
                            break
                    except Exception:
                        pass

                if not next_clicked:
                    break

            # Could not complete
            outcome.error_reason = (
                f"Workday form partially filled but could not submit automatically. "
                f"Complete manually: {page.url}"
            )

        except Exception as e:
            logs.append(f"WorkdayAdapter error: {e}")
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
