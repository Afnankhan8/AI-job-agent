"""
session_manager.py — Persistent browser session management.

Handles ONE upfront login per platform before the apply loop starts.
Because we use a persistent BrowserContext (browser_profile/), cookies
survive across runs — subsequent runs skip the login page entirely.

Auto-login: if session expires mid-run, automatically re-logs in using
credentials from config/user_profile.json. No manual steps required.
"""

import time
import re
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page

from config.profile_loader import CandidateProfile


class SessionManager:
    """
    Manages persistent login sessions for all job platforms.

    Login happens ONCE per platform (or auto-re-login on session expiry).
    Cookies are stored in browser_profile/ and reused on subsequent runs.
    """

    LINKEDIN_HOME  = "https://www.linkedin.com"
    LINKEDIN_LOGIN = "https://www.linkedin.com/login"
    LINKEDIN_FEED  = "https://www.linkedin.com/feed"

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
        Auto-logs in if session expired. Returns True if logged in.
        """
        self._log("Checking LinkedIn session...")
        if self.is_linkedin_logged_in():
            self._log("LinkedIn: already logged in ✓")
            self._linkedin_ok = True
            return True

        self._log("LinkedIn: session expired — auto-logging in with stored credentials...")
        success = self._login_linkedin()
        self._linkedin_ok = success
        if success:
            self._log("LinkedIn: auto-login successful ✓")
        else:
            self._log("LinkedIn: auto-login failed ✗ — check credentials in config/user_profile.json")
        return success

    def re_login_linkedin(self) -> bool:
        """
        Force a fresh LinkedIn login even if session appears active.
        Called by adapters when they hit an authwall mid-apply.
        """
        self._log("LinkedIn: forced re-login triggered...")
        self._linkedin_ok = False
        success = self._login_linkedin()
        self._linkedin_ok = success
        return success

    def is_linkedin_logged_in(self) -> bool:
        """Quick check — open feed page, see if the nav profile icon is visible."""
        page = self._new_page()
        try:
            page.goto(self.LINKEDIN_FEED, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            url = page.url
            if any(x in url for x in ["/login", "/signup", "/authwall", "/uas/login"]):
                return False
            # LinkedIn's navigation markup changes frequently; reaching the
            # authenticated feed is a stronger signal than a profile icon selector.
            if "/feed" in urlparse(url).path:
                return True
            logged_in = page.locator(
                "[data-control-name='nav.homepage'], "
                ".global-nav__me-photo, "
                "img.nav-item__profile-member-photo, "
                "[aria-label='Home'], "
                ".feed-identity-module"
            ).first.is_visible(timeout=4000)
            return logged_in
        except Exception:
            return False
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ── Login implementations ──────────────────────────────────────────────────

    def _login_linkedin(self) -> bool:
        """
        Fully automatic LinkedIn login:
          1. Navigate to /login
          2. Fill email + password from profile
          3. Submit and wait for redirect
          4. Detect CAPTCHA/2FA and wait for the user's verification
          5. Verify logged in via feed URL
        """
        p = self.profile.personal
        if not p.linkedin_email or not p.linkedin_password:
            self._log("LinkedIn credentials not set in profile — skipping.")
            return False

        page = self._new_page()
        try:
            self._log(f"Opening LinkedIn login page ({self.LINKEDIN_LOGIN})...")
            page.goto(self.LINKEDIN_LOGIN, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            # ── Check if we got redirected to the Sign Up ("Join") page ───────
            if "signup" in page.url:
                self._log("Detected Sign Up form. Attempting to click 'Sign in'...")
                try:
                    # Attempt to click any element with text "Sign in"
                    page.locator("text=Sign in").last.click(timeout=3000)
                    page.wait_for_timeout(3000)
                except Exception as e:
                    pass
                
            # SANITY CHECK: Are we still stuck on the Join form?
            if "signup" in page.url:
                self._log("ERROR: Stuck on the sign-up form. Cannot proceed with login.")
                return False
                
            # ── Fill email ────────────────────────────────────────────────────
            email_sel = (
                "input#username, "
                "input[name='session_key'], "
                "input[type='email'], "
                "input#email-or-phone"
            )
            email_filled = False
            for inp in page.locator(email_sel).all():
                if inp.is_visible():
                    # LinkedIn/Chrome may autofill this field before Playwright
                    # reaches it; fill replaces the value instead of appending.
                    inp.fill(p.linkedin_email)
                    email_filled = True
                    break
            if not email_filled:
                self._log("ERROR: Could not find visible email field.")
                return False

            # ── Fill password ─────────────────────────────────────────────────
            password_sel = (
                "input#password, "
                "input[name='session_password'], "
                "input[type='password']"
            )
            pwd_filled = False
            for inp in page.locator(password_sel).all():
                if inp.is_visible():
                    inp.fill(p.linkedin_password)
                    pwd_filled = True
                    break
            if not pwd_filled:
                self._log("ERROR: Could not find visible password field.")
                return False

            # ── Submit ─────────────────────────────────────────────────────────
            try:
                # Just press Enter while focused on the password field
                page.keyboard.press("Enter")
                submit_clicked = True
                self._log("Login submitted via Enter key — waiting for redirect...")
            except Exception as e:
                self._log(f"Error submitting form: {e}")
                return False
            page.wait_for_timeout(5000)

            # ── Handle post-login challenges ───────────────────────────────────
            challenge_notice = False
            for _ in range(150):  # 150 x 2s = 5 minutes maximum
                path = urlparse(page.url).path

                if "/feed" in path or "/jobs" in path or "/in/" in path:
                    self._log("Successfully logged in.")
                    self._linkedin_ok = True
                    return True

                if "/login" in path:
                    err_visible = page.locator(
                        ".alert--error, [data-test-id='error-message'], "
                        ".login__error, form .error"
                    ).first.is_visible(timeout=2000)
                    if err_visible:
                        self._log("Login failed — incorrect credentials or account locked.")
                        return False

                is_challenge = any(
                    marker in path
                    for marker in ("challenge", "checkpoint", "verification", "two-step", "otp")
                )
                if is_challenge and not challenge_notice:
                    challenge_notice = True
                    self._log("LinkedIn verification required. Complete it in the visible browser window.")
                    print("\n>>> LINKEDIN VERIFICATION: Complete it in the visible browser window. <<<\n")

                page.wait_for_timeout(2000)

            if challenge_notice:
                self._log("LinkedIn verification timed out. Run login again and complete the challenge.")
                return False

            # Still not on a logged-in page: make one final navigation attempt.
            page.goto(self.LINKEDIN_FEED, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            # Final URL check
            if any(x in page.url for x in ["/feed", "/jobs"]):
                self._log("LinkedIn login verified via feed navigation ✓")
                return True

            self._log(f"LinkedIn login failed. Final URL: {page.url}")
            return False

        except Exception as e:
            self._log(f"LinkedIn login error: {e}")
            return False
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _new_page(self) -> Page:
        """Open a new page with stealth applied."""
        page = self.context.new_page()
        
        # Aggressively override webdriver properties to bypass LinkedIn's silent bot trap
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {}
            };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
        """)
        
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
        return page
