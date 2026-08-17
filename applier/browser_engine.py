"""
Playwright Browser Engine — Auto-Apply.

For each job:
  1. Resolve the aggregator redirect URL to the final destination.
  2. Open the page in a real Chromium browser.
  3. Detect and fill the application form.
  4. Optionally submit.
  5. Take a screenshot as proof.
  6. Return an ApplicationOutcome.

Two classes of results:
  APPLIED        — form filled AND submitted successfully
  DRY_RUN        — form filled, submission skipped (--dry-run flag)
  REQUIRES_MANUAL — form found but couldn't submit, or no form detected
                    (the URL is saved so you can open it yourself and click Submit)
  FAILED         — browser crash or unrecoverable error
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext
from playwright_stealth import Stealth

from applier.form_filler import HeuristicFormFiller, FormFillResult
from applier.url_resolver import resolve_url
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


@dataclass
class ApplicationOutcome:
    job_id: int
    status: str           # APPLIED | DRY_RUN | REQUIRES_MANUAL | FAILED
    redirect_url: str
    screenshot_path: Optional[str] = None
    error_reason: Optional[str] = None
    logs: List[str] = field(default_factory=list)


class ApplicationEngine:
    def __init__(
        self,
        profile: CandidateProfile,
        context: BrowserContext,
        dry_run: bool = False,
        screenshots_dir: str = "applications/screenshots",
    ):
        self.profile = profile
        self.context = context
        self.dry_run = dry_run
        self.screenshots_dir = screenshots_dir
        self.ai_cover_letter = None  # Set by auto_apply.py before each job
        Path(screenshots_dir).mkdir(parents=True, exist_ok=True)

    def apply(self, job: JobRecord) -> ApplicationOutcome:
        logs: List[str] = []
        logs.append(f"Job #{job.id}: '{job.title}' @ '{job.company or 'Unknown'}'")
        logs.append(f"Source URL: {job.url}")

        if not job.url:
            return ApplicationOutcome(
                job_id=job.id, status="FAILED", redirect_url="",
                error_reason="No URL for this job.", logs=logs,
            )

        # Resolve redirect
        final_url, _ = resolve_url(job.url)
        logs.append(f"Resolved URL: {final_url}")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(self.screenshots_dir, f"job_{job.id}_{timestamp}.png")

        outcome = ApplicationOutcome(
            job_id=job.id,
            status="FAILED",
            redirect_url=final_url,
            screenshot_path=screenshot_path,
            logs=logs,
        )

        try:
            page: Page = self.context.new_page()
            try:
                # Apply stealth directly to the page
                Stealth().apply_stealth_sync(page)
            except Exception as e:
                logs.append(f"Stealth apply failed (ignoring): {e}")

            logs.append(f"Opening: {final_url}")
            page.goto(final_url, wait_until="domcontentloaded", timeout=30000)
            
            # Cloudflare wait logic (up to 80 seconds for manual resolution)
            cf_checks = 0
            while cf_checks < 40:
                try:
                    html = page.content().lower()
                except Exception:
                    # Page is navigating, wait a bit and retry
                    page.wait_for_timeout(2000)
                    cf_checks += 1
                    continue
                
                if "performing security verification" in html or "verify you are human" in html or "cloudflare" in html:
                    if cf_checks == 0:
                        logs.append("🛑 Cloudflare CAPTCHA detected! Please solve it in the browser window...")
                        print("\n>>> CLOUDFLARE DETECTED: You have 80 seconds to click the 'Verify you are human' box in the browser! <<<\n")
                    page.wait_for_timeout(2000)
                    cf_checks += 1
                else:
                    if cf_checks > 0:
                        logs.append("✅ Cloudflare passed!")
                        print(">>> Cloudflare passed! Continuing... <<<")
                        page.wait_for_timeout(3000)
                    break
            
            if cf_checks >= 40:
                logs.append("Timeout waiting for Cloudflare resolution.")
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "Blocked by Cloudflare CAPTCHA. Need manual resolution."
                page.screenshot(path=screenshot_path, full_page=True)
                page.close()
                return outcome

            landed_url = page.url
            outcome.redirect_url = landed_url
            logs.append(f"Landed on: {landed_url}")

            # LinkedIn specific redirect handler
            if "linkedin.com" in landed_url:
                logs.append("Handling LinkedIn page...")
                try:
                    # First, check if we got redirected to a full-page signup/login wall (Apply button missing)
                    apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply'), button:has-text('التقدم'), a:has-text('التقدم'), a.jobs-apply-button").first
                    
                    if not apply_btn.is_visible(timeout=3000):
                        # Apply button not found. See if there's a "Sign in" link on a wall
                        sign_in_link = page.locator("a:has-text('Sign in'), a:has-text('تسجيل الدخول'), a.main__sign-in-link").first
                        if sign_in_link.is_visible(timeout=2000):
                            logs.append("Found 'Sign in' link on wall, clicking it...")
                            sign_in_link.click(force=True)
                            page.wait_for_timeout(2000)
                    
                    # Dismiss sign-in modal if present
                    modal_dismiss = page.locator("button.modal__dismiss, button.contextual-sign-in-modal__modal-dismiss, button.icon-close").first
                    if modal_dismiss.is_visible(timeout=2000):
                        modal_dismiss.click()
                        page.wait_for_timeout(1000)
                    
                    # Re-check Apply button
                    apply_btn = page.locator("button:has-text('Apply'), a:has-text('Apply'), button:has-text('التقدم'), a:has-text('التقدم'), a.jobs-apply-button").first
                    if apply_btn.is_visible(timeout=2000):
                        try:
                            with self.context.expect_page(timeout=5000) as new_page_info:
                                apply_btn.click()
                            new_page = new_page_info.value
                            new_page.wait_for_load_state()
                            logs.append(f"Redirected from LinkedIn to new tab: {new_page.url}")
                            page.close()
                            page = new_page
                        except Exception:
                            # Same-page redirect or login modal
                            page.wait_for_load_state()
                    
                    # Check if a login form is visible (either modal or full page)
                    login_form_visible = False
                    for frame in [page] + page.frames:
                        try:
                            if frame.locator("input[name='session_key'], input[id='username'], input[type='email'], #email-or-phone").first.is_visible(timeout=500) or \
                               frame.locator(".sign-up-modal__outlet, #join-modal, .contextual-sign-in-modal, #organic-sign-in-form").first.is_visible(timeout=500):
                                login_form_visible = True
                                break
                        except Exception:
                            pass
                            
                    if login_form_visible:
                        if self.profile.personal.linkedin_password:
                            logs.append("Auto-logging into LinkedIn using profile credentials...")
                            try:
                                email_input = None
                                active_frame = page
                                for frame in [page] + page.frames:
                                    try:
                                        inp = frame.locator("input[name='session_key'], input[id='username'], input[type='email'], #email-or-phone").first
                                        if inp.is_visible(timeout=500):
                                            email_input = inp
                                            active_frame = frame
                                            break
                                    except Exception:
                                        pass
                                
                                # If email input is not visible, check for the "Sign in with Email" button first
                                if email_input is None:
                                    try:
                                        clicked = False
                                        for frame in [page] + page.frames:
                                            if clicked: break
                                            try:
                                                btn1 = frame.locator("button.contextual-sign-in-modal__sign-in-with-email-cta, a.contextual-sign-in-modal__sign-in-with-email-cta, text='Sign in with Email', text='تسجيل الدخول باستخدام البريد الإلكتروني'").first
                                                btn2 = frame.get_by_text("Sign in with Email", exact=False).first
                                                btn3 = frame.locator("xpath=//a[contains(., 'Sign in with Email')] | //button[contains(., 'Sign in with Email')]").first
                                                
                                                if btn1.is_visible(timeout=1000):
                                                    btn1.click(force=True)
                                                    clicked = True
                                                    active_frame = frame
                                                elif btn2.is_visible(timeout=1000):
                                                    btn2.click(force=True)
                                                    clicked = True
                                                    active_frame = frame
                                                elif btn3.is_visible(timeout=1000):
                                                    btn3.click(force=True)
                                                    clicked = True
                                                    active_frame = frame
                                            except Exception:
                                                continue
                                        page.wait_for_timeout(1500)
                                        
                                        # Re-find email input after clicking
                                        for frame in [page] + page.frames:
                                            try:
                                                inp = frame.locator("input[name='session_key'], input[id='username'], input[type='email'], #email-or-phone").first
                                                if inp.is_visible(timeout=500):
                                                    email_input = inp
                                                    active_frame = frame
                                                    break
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass

                                if email_input is not None:
                                    pass_input = active_frame.locator("input[name='session_password'], input[id='password'], input[type='password'], #password").first
                                    submit_btn = active_frame.locator("button[type='submit'], .csm-v2_sign-in-submit-btn, button:has-text('Sign in'), button:has-text('تسجيل الدخول')").first
                                    
                                    email_input.fill(self.profile.personal.linkedin_email)
                                    pass_input.fill(self.profile.personal.linkedin_password)
                                    submit_btn.click()
                                    
                                    # Wait for login to complete
                                    page.wait_for_timeout(6000)
                                    
                                    # If we were on a wall and not the job page, navigate back to the job page
                                    if "linkedin.com/jobs/view" not in page.url:
                                        logs.append(f"Navigating back to job URL after login: {final_url}")
                                        page.goto(final_url, wait_until="domcontentloaded", timeout=30000)
                                        page.wait_for_timeout(3000)
                                        
                                    logs.append("Auto-login submitted and on job page.")
                                else:
                                    logs.append("Could not find login inputs in modal or wall.")
                            except Exception as e:
                                logs.append(f"Auto-login failed: {e}")
                        else:
                            logs.append("No LinkedIn password provided in profile. Skipping auto-login.")
                            
                    if "linkedin.com" in page.url and apply_btn.is_visible(timeout=2000):
                        try:
                            with self.context.expect_page(timeout=5000) as new_page_info:
                                apply_btn.click()
                            new_page = new_page_info.value
                            new_page.wait_for_load_state()
                            logs.append(f"Redirected from LinkedIn to new tab after login: {new_page.url}")
                            page.close()
                            page = new_page
                        except Exception:
                            logs.append(f"No new tab opened after login, staying on page: {page.url}")
                    
                    landed_url = page.url
                    outcome.redirect_url = landed_url
                except Exception as e:
                    logs.append(f"Failed to navigate LinkedIn apply button: {e}")

            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

            filler = HeuristicFormFiller(
                self.profile,
                dry_run=self.dry_run,
                ai_cover_letter=self.ai_cover_letter,
            )
            fill: FormFillResult = filler.fill_form(page)
            logs.extend(fill.logs)

            # Screenshot
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                logs.append(f"Screenshot saved: {screenshot_path}")
            except Exception as e:
                logs.append(f"Screenshot failed: {e}")

            page.close()

            # Map fill result → outcome status
            if fill.status == "NO_FORM_FOUND":
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = (
                    f"No application form detected at {landed_url}. "
                    "Open the URL manually to apply."
                )
            elif fill.status == "MANUAL_REQUIRED":
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = fill.requires_manual_reason
            elif fill.status == "SUCCESS":
                outcome.status = "DRY_RUN" if self.dry_run else "APPLIED"
            elif fill.status == "PARTIAL":
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = "Partial form fill — manual submission needed."
            else:
                outcome.status = "REQUIRES_MANUAL"
                outcome.error_reason = f"Unexpected fill status: {fill.status}"

        except Exception as err:
            logs.append(f"Engine error: {err}")
            outcome.status = "FAILED"
            outcome.error_reason = str(err)

        return outcome
