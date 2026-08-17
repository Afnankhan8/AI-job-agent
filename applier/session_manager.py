"""
session_manager.py — Persistent browser session management.

Handles ONE upfront login per platform before the apply loop starts.
Because we use a persistent BrowserContext (browser_profile/), cookies
survive across runs — subsequent runs skip the login page entirely.

Usage:
    from applier.session_manager import SessionManager
    manager = SessionManager(context, profile)
    manager.ensure_all_sessions()          # logs in to all needed platforms
    manager.ensure_linkedin()              # only LinkedIn
    is_logged = manager.is_linkedin_logged_in()
"""

import time
from typing import Optional

from playwright.sync_api import BrowserContext, Page

from config.profile_loader import CandidateProfile


class SessionManager:
    """
    Manages persistent login sessions for all job platforms.

    Login happens ONCE per platform. Cookies are stored in the persistent
    browser_profile/ directory and reused on subsequent runs.
    """

    LINKEDIN_HOME = "https://www.linkedin.com"
    LINKEDIN_LOGIN = "https://www.linkedin.com/login"
    LINKEDIN_FEED = "https://www.linkedin.com/feed"

    def __init__(self, context: BrowserContext, profile: CandidateProfile, verbose: bool = True):
        self.context = context
        self.profile = profile
        self.verbose = verbose
        self._linkedin_ok = False

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [session] {msg}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def ensure_all_sessions(self) -> dict:
        """
        Ensure login sessions for all platforms in the profile.
        Returns dict of {platform: True/False}.
        """
        results = {}
        if self.profile.personal.linkedin_email and self.profile.personal.linkedin_password:
            results["linkedin"] = self.ensure_linkedin()
        return results

    def ensure_linkedin(self) -> bool:
        """
        Ensure we have a live LinkedIn session.
        Returns True if logged in (either already was, or just logged in now).
        """
        self._log("Checking LinkedIn session...")
        if self.is_linkedin_logged_in():
            self._log("LinkedIn: already logged in (session cookie active).")
            self._linkedin_ok = True
            return True

        self._log("LinkedIn: not logged in — attempting login now...")
        success = self._login_linkedin()
        self._linkedin_ok = success
        return success

    def is_linkedin_logged_in(self) -> bool:
        """Quick check — open feed page, see if the nav profile icon is visible."""
        page = self._new_page()
        try:
            page.goto(self.LINKEDIN_FEED, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            url = page.url
            # If we got redirected to login/signup, we're not logged in
            if "/login" in url or "/signup" in url or "/authwall" in url:
                return False
            # Check for a nav element that only appears when logged in
            logged_in = page.locator(
                "[data-control-name='nav.homepage'], "
                ".global-nav__me-photo, "
                "img.nav-item__profile-member-photo, "
                "[aria-label='Home']"
            ).first.is_visible(timeout=3000)
            return logged_in
        except Exception:
            return False
        finally:
            page.close()

    # ── Login implementations ──────────────────────────────────────────────────

    def _login_linkedin(self) -> bool:
        """
        Full LinkedIn login flow:
          1. Navigate to /login
          2. Fill email + password
          3. Submit and wait for redirect
          4. Handle 2FA or CAPTCHA if present (pause for manual resolution)
          5. Verify logged in by checking the feed URL
        """
        p = self.profile.personal
        if not p.linkedin_email or not p.linkedin_password:
            self._log("LinkedIn credentials not set in profile — skipping.")
            return False

        page = self._new_page()
        try:
            self._log(f"Navigating to {self.LINKEDIN_LOGIN}...")
            page.goto(self.LINKEDIN_LOGIN, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)

            # Fill email (target visible email/username input)
            email_sel = (
                "input#username:visible, "
                "input[name='session_key']:visible, "
                "input[autocomplete='username']:visible, "
                "input[type='email']:visible"
            )
            page.wait_for_selector(email_sel, timeout=8000)
            page.locator(email_sel).first.fill(p.linkedin_email)
            page.wait_for_timeout(500)

            # Fill password (target visible password input)
            pass_sel = (
                "input#password:visible, "
                "input[name='session_password']:visible, "
                "input[autocomplete='current-password']:visible, "
                "input[type='password']:visible"
            )
            page.locator(pass_sel).first.fill(p.linkedin_password)
            page.wait_for_timeout(500)

            # Submit
            self._log("Submitting login form...")
            submit_sel = (
                "button[type='submit']:visible, "
                "button[data-litms-control-urn='login-submit']:visible, "
                "form button.btn__primary--large:visible"
            )
            page.locator(submit_sel).first.click()
            page.wait_for_timeout(4000)

            # Handle post-login states
            current_url = page.url

            # CAPTCHA / verification challenge
            if "checkpoint" in current_url or "challenge" in current_url:
                self._log(
                    "  ⚠️  LinkedIn verification challenge detected!\n"
                    "  Please complete the verification in the browser window.\n"
                    "  Waiting up to 120 seconds..."
                )
                print("\n>>> LINKEDIN CHALLENGE: Please complete the verification in the browser! <<<\n")
                for _ in range(60):
                    page.wait_for_timeout(2000)
                    if "/feed" in page.url or "/jobs" in page.url:
                        break
                    if "/login" in page.url:
                        self._log("Still on login page — credentials may be wrong.")
                        return False

            # 2FA email/app code
            if "verification" in page.url or "two-step" in page.url:
                self._log(
                    "  ⚠️  LinkedIn 2FA detected!\n"
                    "  Please enter the verification code in the browser.\n"
                    "  Waiting up to 120 seconds..."
                )
                print("\n>>> LINKEDIN 2FA: Enter your verification code in the browser! <<<\n")
                for _ in range(60):
                    page.wait_for_timeout(2000)
                    if "/feed" in page.url or "/jobs" in page.url:
                        break

            # Verify success
            final_url = page.url
            if "/feed" in final_url or "/jobs" in final_url or "linkedin.com/in/" in final_url:
                self._log("LinkedIn login successful!")
                return True
            else:
                # Try one more navigation to feed
                page.goto(self.LINKEDIN_FEED, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                if "/login" not in page.url and "/signup" not in page.url:
                    self._log("LinkedIn login verified via feed navigation.")
                    return True
                self._log(f"LinkedIn login failed. Final URL: {page.url}")
                return False

        except Exception as e:
            self._log(f"LinkedIn login error: {e}")
            return False
        finally:
            page.close()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _new_page(self) -> Page:
        """Open a new page with stealth applied."""
        page = self.context.new_page()
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
        return page
