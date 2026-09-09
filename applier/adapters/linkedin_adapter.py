"""
linkedin_adapter.py — LinkedIn Easy Apply + External Apply handler.

Full automatic pipeline:
  1. Auto-login if session expired (using stored credentials — no manual steps)
  2. Navigate to job page with scroll & retry
  3. Detect Easy Apply OR external apply button
  4. Easy Apply: navigate multi-step modal, fill all fields, submit
  5. External Apply: follow link → detect platform → dispatch to correct adapter
  6. Unknown screening questions answered by Claude AI or safe defaults
  7. Record outcome in DB
"""

import os
import re
import time
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from playwright.sync_api import BrowserContext, Page

from applier.adapters.base_adapter import BaseAdapter, AdapterOutcome
from applier.form_filler import HeuristicFormFiller, _has
from config.profile_loader import CandidateProfile
from database.repository import JobRecord


class LinkedInAdapter(BaseAdapter):
    name = "linkedin"

    # ── Selectors (multiple variants for resilience) ───────────────────────────
    _EASY_APPLY_BTN = (
        "button.jobs-apply-button:has-text('Easy Apply'), "
        "button[aria-label*='Easy Apply'], "
        "button:has-text('Easy Apply'), "
        ".jobs-s-apply button:has-text('Easy'), "
        "[data-job-id] button:has-text('Easy')"
    )
    _EXTERNAL_APPLY_BTN = (
        "button.jobs-apply-button:not(:has-text('Easy Apply')), "
        "a.jobs-apply-button, "
        "button.jobs-apply-button:has-text('Apply'), "
        ".jobs-s-apply a[href], "
        ".jobs-unified-top-card__content--two-pane a.jobs-apply-button"
    )
    _MODAL = (
        ".jobs-easy-apply-modal, "
        "[data-test-modal-id='easy-apply-modal'], "
        "div[role='dialog']:has(.jobs-easy-apply-content), "
        ".artdeco-modal:has(button:has-text('Next')), "
        ".artdeco-modal:has(button:has-text('Submit application'))"
    )
    _NEXT_BTN = (
        "footer button.artdeco-button--primary:has-text('Next'), "
        "button[aria-label='Continue to next step'], "
        "button:has-text('Next'), "
        ".jobs-easy-apply-footer button.artdeco-button--primary"
    )
    _REVIEW_BTN = (
        "button:has-text('Review'), "
        "button[aria-label='Review your application'], "
        "footer button.artdeco-button--primary:has-text('Review')"
    )
    _SUBMIT_BTN = (
        "button:has-text('Submit application'), "
        "button[aria-label='Submit application'], "
        "footer button.artdeco-button--primary:has-text('Submit')"
    )
    _DISMISS_BTN = (
        "button[aria-label='Dismiss'], "
        "button.artdeco-modal__dismiss, "
        "button.modal__dismiss, "
        "button[data-test-modal-close-btn], "
        "button[aria-label='Close'], "
        "button:has-text('Not now'), "
        "button:has-text('Skip')"
    )
    _ALREADY_APPLIED = (
        "button[aria-label='You applied']:visible, "
        ".artdeco-inline-feedback:has-text('Applied'), "
        "span:has-text('Applied'):visible"
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
        _session_manager=None,
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

            # ── 2. Auto-handle auth wall ───────────────────────────────────────
            if self._is_auth_wall(page.url):
                logs.append("Auth wall hit — auto re-logging in...")
                # Try to re-login using session manager
                if _session_manager is not None:
                    ok = _session_manager.re_login_linkedin()
                else:
                    # Fallback: do inline login
                    ok = self._inline_login(page, profile, logs)

                if ok:
                    logs.append("Re-login succeeded — retrying navigation.")
                    page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                else:
                    logs.append("Re-login failed — check credentials in user_profile.json")
                    outcome.status = "FAILED"
                    outcome.error_reason = "Auto-login failed. Check email/password in config/user_profile.json"
                    self._screenshot(page, screenshot_path)
                    return outcome

            # ── 3. Still on auth wall? ─────────────────────────────────────────
            if self._is_auth_wall(page.url):
                logs.append("Still on auth wall after re-login.")
                outcome.status = "FAILED"
                outcome.error_reason = "Could not authenticate with LinkedIn."
                self._screenshot(page, screenshot_path)
                return outcome

            outcome.redirect_url = page.url

            # ── 4. Scroll to make apply button visible ─────────────────────────
            self._scroll_to_apply(page, logs)

            # ── 5. Dismiss any overlay modals ──────────────────────────────────
            self._dismiss_modals(page, logs)

            # ── 6. Check if already applied ────────────────────────────────────
            if self._is_visible(page, self._ALREADY_APPLIED, timeout=2000):
                logs.append("Already applied to this job — skipping.")
                outcome.status = "APPLIED"
                outcome.error_reason = "Already applied (detected on page)."
                self._screenshot(page, screenshot_path)
                return outcome

            # ── 7. Find the Apply button ───────────────────────────────────────
            easy_apply_visible = self._is_visible(page, self._EASY_APPLY_BTN, timeout=5000)
            external_visible   = (
                self._is_visible(page, self._EXTERNAL_APPLY_BTN, timeout=3000)
                if not easy_apply_visible else False
            )

            # ── 8. Easy Apply flow ─────────────────────────────────────────────
            if easy_apply_visible:
                logs.append("Easy Apply button found — starting Easy Apply flow.")
                result = self._do_easy_apply(
                    page, profile, context, dry_run,
                    ai_cover_letter, ai_answers, ai_client, logs, screenshot_path,
                )
                outcome.status = result
                if result == "REQUIRES_MANUAL":
                    outcome.error_reason = "Easy Apply flow could not complete automatically."

            # ── 9. External Apply: follow link + dispatch to correct adapter ───
            elif external_visible:
                logs.append("External apply button found — following link...")
                result, redirect = self._do_external_apply(
                    page, job, profile, context, dry_run,
                    ai_cover_letter, ai_answers, ai_client,
                    screenshots_dir, logs,
                )
                outcome.status = result
                outcome.redirect_url = redirect or outcome.redirect_url
                if result in ("REQUIRES_MANUAL", "FAILED"):
                    outcome.error_reason = f"External apply: {result} at {redirect}"

            # ── 10. No button found — scroll more and retry ────────────────────
            else:
                logs.append("No apply button on initial view — scrolling + retrying...")
                # Scroll in steps and recheck
                found = False
                for scroll_pct in [0.3, 0.5, 0.7]:
                    page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_pct})")
                    page.wait_for_timeout(1500)
                    self._dismiss_modals(page, logs)
                    if self._is_visible(page, self._EASY_APPLY_BTN, timeout=3000):
                        logs.append(f"Easy Apply button found after scrolling to {int(scroll_pct*100)}%.")
                        result = self._do_easy_apply(
                            page, profile, context, dry_run,
                            ai_cover_letter, ai_answers, ai_client, logs, screenshot_path,
                        )
                        outcome.status = result
                        found = True
                        break
                    elif self._is_visible(page, self._EXTERNAL_APPLY_BTN, timeout=2000):
                        logs.append(f"External button found after scrolling to {int(scroll_pct*100)}%.")
                        result, redirect = self._do_external_apply(
                            page, job, profile, context, dry_run,
                            ai_cover_letter, ai_answers, ai_client,
                            screenshots_dir, logs,
                        )
                        outcome.status = result
                        outcome.redirect_url = redirect or outcome.redirect_url
                        found = True
                        break

                if not found:
                    logs.append("Apply button not found after full page scroll.")
                    outcome.status = "REQUIRES_MANUAL"
                    outcome.error_reason = (
                        "No Apply/Easy Apply button found. "
                        "Job may be expired, filled, or restricted to certain applicants."
                    )

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

    # ── External Apply: follow link → dispatch to correct adapter ─────────────

    def _do_external_apply(
        self,
        page: Page,
        job: JobRecord,
        profile: CandidateProfile,
        context: BrowserContext,
        dry_run: bool,
        ai_cover_letter: Optional[str],
        ai_answers: Optional[dict],
        ai_client,
        screenshots_dir: str,
        logs: List[str],
    ) -> Tuple[str, str]:
        """
        Click the external Apply button, follow to the real ATS page,
        then dispatch to the correct adapter (Greenhouse/Lever/Workday/Generic).
        Returns (status, final_url).
        """
        from applier.adapters import detect_platform, get_adapter
        import copy

        try:
            btn = page.locator(self._EXTERNAL_APPLY_BTN).first

            # Try to get href directly (links open in new tab)
            href = btn.get_attribute("href") or ""
            if href and href.startswith("http"):
                ext_url = href
            else:
                # Click and capture new page
                try:
                    with context.expect_page(timeout=8000) as page_info:
                        btn.click()
                    ext_page = page_info.value
                    ext_page.wait_for_load_state("domcontentloaded", timeout=15000)
                    ext_url = ext_page.url
                    ext_page.close()
                except Exception:
                    # Same-tab navigation
                    btn.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    ext_url = page.url

            logs.append(f"External URL: {ext_url}")

            if not ext_url or "linkedin.com" in ext_url:
                logs.append("External link stayed on LinkedIn — no external form found.")
                return ("REQUIRES_MANUAL", ext_url)

            # Detect platform and dispatch
            platform = detect_platform(ext_url)
            logs.append(f"Detected platform: {platform.upper()}")

            if platform == "linkedin":
                # Shouldn't happen but guard
                return ("REQUIRES_MANUAL", ext_url)

            adapter = get_adapter(platform)

            # Build a mock job with the external URL
            ext_job = copy.copy(job)
            ext_job.url = ext_url

            outcome = adapter.apply(
                job=ext_job,
                profile=profile,
                context=context,
                dry_run=dry_run,
                ai_cover_letter=ai_cover_letter,
                ai_answers=ai_answers or {},
                screenshots_dir=screenshots_dir,
                ai_client=ai_client,
            )
            logs.extend(outcome.logs or [])
            return (outcome.status, outcome.redirect_url or ext_url)

        except Exception as e:
            logs.append(f"External apply error: {e}")
            return ("FAILED", job.url or "")

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
        """Navigate the Easy Apply modal step-by-step. Returns status string."""

        # Click Easy Apply button
        try:
            page.locator(self._EASY_APPLY_BTN).first.click()
            page.wait_for_timeout(2500)
        except Exception as e:
            logs.append(f"Could not click Easy Apply: {e}")
            return "REQUIRES_MANUAL"

        # Wait for modal to appear
        try:
            page.wait_for_selector(self._MODAL, timeout=12000)
            logs.append("Easy Apply modal opened.")
        except Exception:
            logs.append("Easy Apply modal did not open — retrying scroll + click.")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1000)
            try:
                page.locator(self._EASY_APPLY_BTN).first.click()
                page.wait_for_timeout(2500)
                page.wait_for_selector(self._MODAL, timeout=8000)
                logs.append("Easy Apply modal opened on retry.")
            except Exception:
                logs.append("Easy Apply modal could not be opened.")
                return "REQUIRES_MANUAL"

        filler = HeuristicFormFiller(
            profile=profile,
            dry_run=True,   # form_filler never submits — we control submission here
            ai_cover_letter=ai_cover_letter,
            ai_suggested_answers=ai_answers or {},
            ai_client=ai_client,
        )

        resume_path = os.path.abspath(profile.personal.resume_path)
        resume_attached = False

        max_steps = 15
        prev_html = ""

        for step in range(max_steps):
            logs.append(f"Easy Apply step {step + 1}/{max_steps}")
            page.wait_for_timeout(1800)

            # Detect if modal is still open
            if not self._is_visible(page, self._MODAL, timeout=3000):
                logs.append("Modal closed — application may have been submitted.")
                return "APPLIED"

            # Fill all inputs on this step
            resume_attached = self._fill_step(
                page, profile, filler, ai_cover_letter, ai_client,
                resume_path, resume_attached, logs,
            )

            # Check available navigation buttons
            has_submit = self._is_visible(page, self._SUBMIT_BTN, timeout=2000)
            has_review = self._is_visible(page, self._REVIEW_BTN, timeout=2000)
            has_next   = self._is_visible(page, self._NEXT_BTN, timeout=2000)

            if has_submit:
                logs.append("Reached Submit step ✓")
                if dry_run:
                    logs.append("DRY RUN — not clicking Submit.")
                    return "DRY_RUN"
                page.locator(self._SUBMIT_BTN).first.click()
                page.wait_for_timeout(4000)
                logs.append("Application submitted via Easy Apply! ✓")
                self._dismiss_modals(page, logs)
                return "APPLIED"

            elif has_review:
                logs.append("Clicking Review...")
                page.locator(self._REVIEW_BTN).first.click()
                page.wait_for_timeout(2000)

            elif has_next:
                # Check if page actually changed (avoid infinite loop on validation errors)
                current_html = page.locator(self._MODAL).first.inner_html() if self._is_visible(page, self._MODAL, timeout=1000) else ""
                if current_html and current_html == prev_html:
                    logs.append("Page did not advance — possible required field blocking next.")
                    # Try to find and fill the missing required field
                    self._fill_required_fields(page, profile, ai_cover_letter, ai_client, logs)
                prev_html = current_html
                logs.append("Clicking Next...")
                page.locator(self._NEXT_BTN).first.click()
                page.wait_for_timeout(2000)

            else:
                # No navigation buttons — check for validation errors
                logs.append("No navigation button found — checking for validation errors...")
                err_text = self._get_validation_errors(page)
                if err_text:
                    logs.append(f"Validation errors: {err_text[:200]}")
                    # Try to fill required fields
                    self._fill_required_fields(page, profile, ai_cover_letter, ai_client, logs)
                    page.wait_for_timeout(1500)
                    # Retry navigation
                    if self._is_visible(page, self._NEXT_BTN, timeout=2000):
                        page.locator(self._NEXT_BTN).first.click()
                        page.wait_for_timeout(2000)
                    elif self._is_visible(page, self._SUBMIT_BTN, timeout=2000):
                        if not dry_run:
                            page.locator(self._SUBMIT_BTN).first.click()
                            page.wait_for_timeout(4000)
                            return "APPLIED"
                        return "DRY_RUN"
                    else:
                        logs.append("No way to advance — stopping.")
                        break
                else:
                    logs.append("No navigation buttons and no validation errors — stopping.")
                    break

        logs.append("Reached max steps without submitting.")
        return "REQUIRES_MANUAL"

    # ── Step filling ───────────────────────────────────────────────────────────

    def _fill_step(
        self,
        page: Page,
        profile: CandidateProfile,
        filler: HeuristicFormFiller,
        ai_cover_letter: Optional[str],
        ai_client,
        resume_path: str,
        resume_attached: bool,
        logs: List[str],
    ) -> bool:
        """Fill all visible fields on the current Easy Apply step. Returns updated resume_attached."""
        p     = profile.personal
        prefs = profile.work_preferences
        sa    = profile.screening_answers
        cover = ai_cover_letter or profile.cover_letter_template

        try:
            modal = page.locator(self._MODAL).first

            # ── Text inputs & textareas ────────────────────────────────────────
            for inp in modal.locator("input:visible, textarea:visible").all():
                try:
                    itype = (inp.get_attribute("type") or "text").lower()
                    if itype in ("hidden", "submit", "button", "image", "reset", "checkbox"):
                        continue

                    # File upload — resume
                    if itype == "file":
                        if not resume_attached and os.path.isfile(resume_path):
                            inp.set_input_files(resume_path)
                            logs.append("✓ CV uploaded.")
                            resume_attached = True
                        continue

                    # Skip already-filled inputs
                    try:
                        current = inp.input_value()
                        if current and current.strip():
                            continue
                    except Exception:
                        pass

                    meta = self._elem_meta(page, inp)
                    tag  = inp.evaluate("el => el.tagName.toLowerCase()")

                    value = self._map_field(meta, tag, p, prefs, sa, cover, prefs.years_of_experience)

                    if value:
                        inp.fill(value)
                        logs.append(f"✓ Filled '{meta[:50]}' → '{str(value)[:40]}'")
                    elif tag == "textarea":
                        # Unknown textarea — use cover letter
                        inp.fill(cover)
                        logs.append(f"✓ Filled unknown textarea with cover letter.")
                    elif ai_client and meta:
                        # Ask AI for unknown fields
                        ai_val = self._ai_answer(ai_client, meta, profile, logs)
                        if ai_val:
                            inp.fill(ai_val)
                            logs.append(f"✓ AI filled '{meta[:50]}' → '{ai_val[:40]}'")

                except Exception as e:
                    logs.append(f"Field fill error: {e}")

            # ── Select dropdowns ───────────────────────────────────────────────
            for sel in modal.locator("select:visible").all():
                try:
                    meta = self._elem_meta(page, sel)
                    options = sel.evaluate(
                        "el => Array.from(el.options).map(o => ({v: o.value, t: o.text.toLowerCase().trim()}))"
                    )
                    if not options or (len(options) == 1 and options[0]["v"] == ""):
                        continue

                    # Skip already selected (non-default)
                    cur = sel.evaluate("el => el.value")
                    if cur and cur not in ("", "0", "select", "-- select --", "please select"):
                        continue

                    chosen = self._pick_option(meta, options, prefs, sa)

                    if chosen is None and ai_client and meta:
                        ai_val = self._ai_answer(ai_client, meta, profile, logs, options=[o["t"] for o in options])
                        if ai_val:
                            for opt in options:
                                if ai_val.lower() in opt["t"]:
                                    chosen = opt["v"]
                                    break

                    if chosen is not None:
                        sel.select_option(value=chosen)
                        logs.append(f"✓ Select '{meta[:40]}' → '{chosen}'")
                    elif options:
                        # Pick first non-empty option as safe default
                        for opt in options:
                            if opt["v"] not in ("", "0"):
                                sel.select_option(value=opt["v"])
                                logs.append(f"✓ Select '{meta[:40]}' → default '{opt['v']}'")
                                break

                except Exception as e:
                    logs.append(f"Select error: {e}")

            # ── Radio buttons ──────────────────────────────────────────────────
            radio_groups: dict = {}
            for radio in modal.locator("input[type='radio']:visible").all():
                name = radio.get_attribute("name") or radio.get_attribute("id") or ""
                if name:
                    radio_groups.setdefault(name, []).append(radio)

            for name, group in radio_groups.items():
                try:
                    # Skip if already selected
                    if any(r.is_checked() for r in group):
                        continue

                    meta = self._elem_meta(page, group[0])
                    # Also read the question text near the group
                    question = self._read_question_text(page, group[0])
                    full_meta = (meta + " " + question).lower()

                    want = self._pick_radio(full_meta, sa)

                    if want is None and ai_client and full_meta.strip():
                        opts = []
                        for r in group:
                            lbl = self._elem_meta(page, r)
                            v = (r.get_attribute("value") or "").lower()
                            opts.append(lbl or v)
                        ai_val = self._ai_answer(ai_client, question or meta, profile, logs, options=opts)
                        if ai_val:
                            want = ai_val.lower()

                    if want:
                        for radio in group:
                            val = (radio.get_attribute("value") or "").lower()
                            lbl = self._elem_meta(page, radio).lower()
                            if want in val or want in lbl or val in want:
                                radio.check()
                                logs.append(f"✓ Radio '{name[:40]}' → '{want}'")
                                break
                    else:
                        # Default: check the first option
                        group[0].check()
                        logs.append(f"✓ Radio '{name[:40]}' → first option (default)")

                except Exception as e:
                    logs.append(f"Radio error: {e}")

            # ── Checkboxes (optional consent/agreement) ────────────────────────
            for chk in modal.locator("input[type='checkbox']:visible").all():
                try:
                    if chk.is_checked():
                        continue
                    meta = self._elem_meta(page, chk).lower()
                    # Auto-check privacy policy, terms, and consent checkboxes
                    if any(k in meta for k in ["privacy", "terms", "agree", "consent", "policy", "gdpr"]):
                        chk.check()
                        logs.append(f"✓ Checked consent checkbox: '{meta[:50]}'")
                except Exception:
                    pass

        except Exception as e:
            logs.append(f"Step fill error: {e}")

        return resume_attached

    def _fill_required_fields(
        self,
        page: Page,
        profile: CandidateProfile,
        ai_cover_letter: Optional[str],
        ai_client,
        logs: List[str],
    ) -> None:
        """Try to fill any visible required/error-highlighted fields in the modal."""
        cover = ai_cover_letter or profile.cover_letter_template
        p = profile.personal
        try:
            modal = page.locator(self._MODAL).first
            # Find required inputs that are empty
            required = modal.locator(
                "input[required]:visible, textarea[required]:visible, "
                "input[aria-required='true']:visible, textarea[aria-required='true']:visible, "
                ".artdeco-text-input--error input:visible, .fb-dash-form-element--error input:visible"
            ).all()
            for inp in required:
                try:
                    current = inp.input_value()
                    if current and current.strip():
                        continue
                    itype = (inp.get_attribute("type") or "text").lower()
                    if itype == "file":
                        continue
                    meta = self._elem_meta(page, inp)
                    tag  = inp.evaluate("el => el.tagName.toLowerCase()")
                    value = self._map_field(
                        meta, tag, p, profile.work_preferences,
                        profile.screening_answers, cover,
                        profile.work_preferences.years_of_experience
                    )
                    if value:
                        inp.fill(value)
                        logs.append(f"✓ Filled required field '{meta[:50]}'")
                    elif tag == "textarea" or not value:
                        inp.fill(cover[:500])
                        logs.append(f"✓ Filled required field with cover excerpt")
                except Exception:
                    pass
        except Exception as e:
            logs.append(f"Required field fill error: {e}")

    # ── Field mapping ──────────────────────────────────────────────────────────

    def _map_field(self, meta, tag, p, prefs, sa, cover, yoe) -> Optional[str]:
        """Map a field's meta string to a profile value."""
        m = meta.lower()
        if "email" in m:
            return p.email
        if any(k in m for k in ["first name", "firstname", "given name", "first_name"]):
            return p.first_name
        if any(k in m for k in ["last name", "lastname", "surname", "family name", "last_name"]):
            return p.last_name
        if any(k in m for k in ["full name", "fullname", "your name", "name"]) and "last" not in m and "first" not in m:
            return p.full_name
        if any(k in m for k in ["phone", "mobile", "tel", "telephone"]):
            return p.phone
        if "linkedin" in m:
            return p.linkedin_url
        if "github" in m:
            return p.github_url
        if any(k in m for k in ["portfolio", "website", "personal site"]):
            return p.portfolio_url or p.github_url
        if any(k in m for k in ["city", "location", "address", "where are you"]):
            return p.location
        if any(k in m for k in ["cover letter", "coverletter", "motivation", "why do you", "tell us"]):
            return cover
        if any(k in m for k in ["salary", "compensation", "expected", "desired salary"]):
            return prefs.expected_salary
        if any(k in m for k in ["notice", "when can you start", "availability", "available"]):
            return prefs.notice_period
        if any(k in m for k in ["experience", "years of experience", "years"]) and tag == "input":
            return str(yoe)
        if any(k in m for k in ["current title", "job title", "your role"]):
            return prefs.desired_title
        if any(k in m for k in ["current company", "employer", "organization"]):
            return "Open to opportunities"
        return None

    def _pick_option(self, meta, options, prefs, sa) -> Optional[str]:
        """Pick the best select option based on field meta."""
        m = meta.lower()
        if any(k in m for k in ["experience", "years"]):
            yoe = prefs.years_of_experience
            for opt in options:
                nums = re.findall(r"\d+", opt["t"])
                if nums and int(nums[0]) <= yoe:
                    chosen = opt["v"]
            # Prefer highest that fits
            for opt in reversed(options):
                nums = re.findall(r"\d+", opt["t"])
                if nums and int(nums[0]) <= yoe:
                    return opt["v"]
            return options[-1]["v"] if options else None

        if any(k in m for k in ["authorized", "eligible", "right to work", "work authorization"]):
            want = "yes" if sa.get("authorized_to_work", "yes").lower() == "yes" else "no"
            for opt in options:
                if want in opt["t"]:
                    return opt["v"]

        if any(k in m for k in ["sponsor", "sponsorship", "visa"]):
            for opt in options:
                if "no" in opt["t"] or "not required" in opt["t"] or "not need" in opt["t"]:
                    return opt["v"]

        if any(k in m for k in ["notice", "available", "start"]):
            for opt in options:
                if any(k in opt["t"] for k in ["immediately", "asap", "0", "two weeks", "2 weeks"]):
                    return opt["v"]

        if any(k in m for k in ["relocate", "relocation"]):
            want = "yes" if sa.get("willing_to_relocate", "yes").lower() == "yes" else "no"
            for opt in options:
                if want in opt["t"]:
                    return opt["v"]

        if any(k in m for k in ["country", "nationality"]):
            for opt in options:
                if "united arab emirates" in opt["t"] or "uae" in opt["t"] or "india" in opt["t"]:
                    return opt["v"]

        return None

    def _pick_radio(self, meta: str, sa: dict) -> Optional[str]:
        """Pick the correct radio button answer."""
        m = meta.lower()
        if any(k in m for k in ["authorized", "eligible", "right to work", "legally"]):
            return "yes" if sa.get("authorized_to_work", "yes").lower() == "yes" else "no"
        if any(k in m for k in ["sponsor", "sponsorship", "visa"]):
            return "no" if sa.get("require_sponsorship", "no").lower() == "no" else "yes"
        if any(k in m for k in ["relocate", "relocation", "willing to move"]):
            return "yes" if sa.get("willing_to_relocate", "yes").lower() == "yes" else "no"
        if any(k in m for k in ["remote", "work from home", "hybrid"]):
            return "yes"
        if any(k in m for k in ["full time", "full-time"]):
            return "yes"
        return None

    def _ai_answer(self, ai_client, question: str, profile: CandidateProfile, logs: List[str], options: list = None) -> Optional[str]:
        """Use Claude AI to answer an unknown screening question."""
        try:
            opts_str = f"\nOptions: {', '.join(options)}" if options else ""
            prompt = (
                f"You are helping fill a job application form for {profile.personal.full_name}.\n"
                f"Candidate profile summary: {profile.to_summary()}\n\n"
                f"Answer this form question concisely (one short sentence or word):\n"
                f"Question: {question}{opts_str}\n\n"
                f"If it's a yes/no question, reply only with 'yes' or 'no'.\n"
                f"If options are given, reply with the exact option text.\n"
                f"Keep answers short and professional."
            )
            response = ai_client.complete(prompt, max_tokens=80)
            answer = (response or "").strip().strip('"').strip("'")
            return answer if answer else None
        except Exception as e:
            logs.append(f"AI answer error: {e}")
            return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _inline_login(self, page: Page, profile: CandidateProfile, logs: List[str]) -> bool:
        """Inline LinkedIn login on the current page."""
        try:
            p = profile.personal
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            page.locator("input#username, input[name='session_key']").first.fill(p.linkedin_email)
            page.wait_for_timeout(500)
            page.locator("input#password, input[name='session_password']").first.fill(p.linkedin_password)
            page.wait_for_timeout(500)
            page.locator("button[type='submit']").first.click()
            page.wait_for_timeout(5000)

            # Handle challenge
            if "checkpoint" in page.url or "challenge" in page.url or "verification" in page.url:
                logs.append("⚠️  Challenge detected — waiting 60s for manual completion...")
                print("\n>>> LINKEDIN CHALLENGE: Complete verification in the browser window! <<<\n")
                for _ in range(30):
                    page.wait_for_timeout(2000)
                    if any(x in page.url for x in ["/feed", "/jobs"]):
                        return True

            return any(x in page.url for x in ["/feed", "/jobs", "linkedin.com/in/"])
        except Exception as e:
            logs.append(f"Inline login error: {e}")
            return False

    def _scroll_to_apply(self, page: Page, logs: List[str]) -> None:
        """Scroll the page to make the apply button section visible."""
        try:
            # Try to scroll the job detail panel
            page.evaluate("""
                const panels = document.querySelectorAll(
                    '.jobs-unified-top-card, .job-details-jobs-unified-top-card__container, '
                    + '.jobs-details__main-content, .scaffold-layout__main'
                );
                if (panels.length > 0) panels[0].scrollIntoView({block: 'start'});
            """)
            page.wait_for_timeout(800)
        except Exception:
            pass

    def _dismiss_modals(self, page: Page, logs: List[str]) -> None:
        """Dismiss any overlay modals."""
        for sel in [
            "button[aria-label='Dismiss']",
            "button.artdeco-modal__dismiss",
            "button[aria-label='Close']",
            "button[data-test-modal-close-btn]",
            "button:has-text('Not now')",
            "button:has-text('Skip')",
            "button:has-text('No thanks')",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    logs.append(f"Dismissed modal: {sel}")
                    page.wait_for_timeout(600)
            except Exception:
                pass

    def _get_validation_errors(self, page: Page) -> str:
        """Get any visible validation error messages."""
        try:
            errors = page.locator(
                ".artdeco-inline-feedback--error, "
                ".fb-dash-form-element__error-field, "
                "[data-test-form-element-error-message], "
                ".error-message, .invalid-feedback"
            ).all_inner_texts()
            return " | ".join(e.strip() for e in errors if e.strip())
        except Exception:
            return ""

    def _read_question_text(self, page: Page, element) -> str:
        """Read the nearby question/label text for a form element."""
        try:
            return page.evaluate("""
                el => {
                    // Walk up DOM to find a fieldset or question container
                    let node = el.parentElement;
                    for (let i = 0; i < 6; i++) {
                        if (!node) break;
                        const legend = node.querySelector('legend, label, .jobs-easy-apply-form-element__label, span[data-test-form-element-label]');
                        if (legend && legend.innerText.trim()) return legend.innerText.trim();
                        const h3 = node.querySelector('h3, h4, p');
                        if (h3 && h3.innerText.trim()) return h3.innerText.trim();
                        node = node.parentElement;
                    }
                    return '';
                }
            """, element.element_handle() if hasattr(element, "element_handle") else element)
        except Exception:
            return ""

    @staticmethod
    def _is_auth_wall(url: str) -> bool:
        return any(x in url for x in ["/authwall", "/login", "/signup", "/uas/login", "checkpoint"])

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
                    const id   = el.id || '';
                    const name = el.name || el.getAttribute('name') || '';
                    const ph   = el.placeholder || el.getAttribute('aria-label') || '';
                    const lbl  = id
                        ? ((document.querySelector('label[for="' + id + '"]') || {}).innerText || '')
                        : '';
                    const title = el.title || '';
                    return [id, name, ph, lbl, title].join(' ').toLowerCase().trim();
                }""",
                elem.element_handle() if hasattr(elem, "element_handle") else elem,
            )
            return attrs or ""
        except Exception:
            return ""
