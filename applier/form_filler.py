"""
Heuristic Form Filler — Playwright (Enhanced).

Works on open application portals: Greenhouse, Lever, Workday, SmartRecruiters,
and direct company career pages.

Strategy:
  1. Scan all input / textarea / select / radio / checkbox elements
     (including inside iframes).
  2. Use field name, id, placeholder, aria-label, and label text to identify
     what each field expects.
  3. Fill with values from CandidateProfile.
  4. Use AI-generated cover letter if available, otherwise use static template.
  5. Attach resume PDF to any file upload.
  6. Handle dropdowns (select), radio buttons, and checkboxes.
  7. Use Ollama to answer any unrecognized screening question.
  8. Optionally submit the form.

Returns a FormFillResult describing what happened.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import Page, ElementHandle

from config.profile_loader import CandidateProfile


@dataclass
class FormFillResult:
    status: str           # SUCCESS | PARTIAL | MANUAL_REQUIRED | NO_FORM_FOUND
    fields_filled: int = 0
    resume_attached: bool = False
    submitted: bool = False
    requires_manual_reason: Optional[str] = None
    logs: List[str] = field(default_factory=list)


class HeuristicFormFiller:
    def __init__(
        self,
        profile: CandidateProfile,
        dry_run: bool = False,
        ai_cover_letter: Optional[str] = None,
        ai_suggested_answers: Optional[Dict[str, str]] = None,
        ai_client=None,                    # OllamaClient — used for unknown fields
    ):
        self.profile = profile
        self.dry_run = dry_run
        self.ai_cover_letter = ai_cover_letter
        self.ai_suggested_answers = ai_suggested_answers or {}
        self.ai_client = ai_client

    # ── Public entry point ────────────────────────────────────────────────────

    def fill_form(self, page: Page) -> FormFillResult:
        result = FormFillResult(status="NO_FORM_FOUND")
        logs = result.logs
        logs.append(f"Scanning page: {page.url}")

        if self.ai_cover_letter:
            logs.append("Using AI-generated cover letter for this application.")
        else:
            logs.append("Using static cover letter template.")

        # First, try to click an "Apply" button if present
        self._click_apply_button(page, logs)
        page.wait_for_timeout(2000)

        # Scan the main page and every iframe for form elements
        for target in [page, *page.frames]:
            try:
                inputs = target.query_selector_all("input, textarea, select")
                if inputs:
                    logs.append(
                        f"Found {len(inputs)} form elements in "
                        f"frame '{getattr(target, 'name', 'main')}'"
                    )
                    self._fill_inputs(target, inputs, result)
            except Exception as exc:
                logs.append(f"Frame scan error: {exc}")

        # Handle radio buttons and checkboxes separately (not always in query above)
        self._fill_radios_and_checkboxes(page, result)

        # Nothing found
        if result.fields_filled == 0 and not result.resume_attached:
            result.status = "NO_FORM_FOUND"
            result.requires_manual_reason = "No fillable application form found on this page."
            return result

        # CAPTCHA / login wall check
        if self._captcha_present(page):
            result.status = "MANUAL_REQUIRED"
            result.requires_manual_reason = "CAPTCHA or login wall detected — manual action required."
            return result

        # Submit or dry-run
        if self.dry_run:
            logs.append("DRY RUN: form filled but NOT submitted.")
            result.status = "SUCCESS"
        else:
            submitted = self._submit_form(page, logs)
            result.submitted = submitted
            result.status = "SUCCESS" if submitted else "PARTIAL"

        return result

    # ── Filling logic ─────────────────────────────────────────────────────────

    def _fill_inputs(self, target, inputs: List[ElementHandle], result: FormFillResult):
        p = self.profile.personal
        prefs = self.profile.work_preferences
        resume_path = os.path.abspath(p.resume_path)
        resume_ok = os.path.isfile(resume_path)
        cover_letter = self.ai_cover_letter or self.profile.cover_letter_template

        for el in inputs:
            try:
                if not el.is_visible():
                    continue

                tag   = el.tag_name.lower()
                itype = (el.get_attribute("type") or "text").lower()
                meta  = self._meta(target, el)

                # ── File upload / resume ──────────────────────────────────────
                if itype == "file" or _has(meta, "resume", "cv", "curriculum vitae"):
                    if resume_ok and not result.resume_attached:
                        el.set_input_files(resume_path)
                        result.resume_attached = True
                        result.fields_filled += 1
                        result.logs.append(f"Resume attached: {resume_path}")
                    continue

                # ── Hidden / non-fillable ─────────────────────────────────────
                if itype in ("hidden", "submit", "button", "image", "reset"):
                    continue

                # ── Checkbox ─────────────────────────────────────────────────
                if itype == "checkbox":
                    self._handle_checkbox(el, meta, result)
                    continue

                # ── Radio ──────────────────────────────────────────────────────
                if itype == "radio":
                    # Handled in _fill_radios_and_checkboxes
                    continue

                # ── Select / dropdown ─────────────────────────────────────────
                if tag == "select":
                    self._handle_select(target, el, meta, result)
                    continue

                # ── Number ───────────────────────────────────────────────────
                if itype == "number":
                    self._handle_number(el, meta, prefs, result)
                    continue

                # ── Text inputs ───────────────────────────────────────────────
                current = el.input_value() if tag != "select" else ""

                if itype == "email" or _has(meta, "email"):
                    if not current:
                        el.fill(p.email)
                        result.fields_filled += 1
                        result.logs.append(f"Email: {p.email}")

                elif _has(meta, "first_name", "firstname", "first name", "given name", "given-name", "forename"):
                    if not current:
                        el.fill(p.first_name)
                        result.fields_filled += 1
                        result.logs.append(f"First name: {p.first_name}")

                elif _has(meta, "last_name", "lastname", "last name", "surname", "family name", "family-name"):
                    if not current:
                        el.fill(p.last_name)
                        result.fields_filled += 1
                        result.logs.append(f"Last name: {p.last_name}")

                elif _has(meta, "full name", "fullname", "full_name", "your name", "applicant name") and not _has(meta, "company"):
                    if not current:
                        el.fill(p.full_name)
                        result.fields_filled += 1
                        result.logs.append(f"Full name: {p.full_name}")

                elif itype == "tel" or _has(meta, "phone", "mobile", "tel", "contact number", "cell"):
                    if not current:
                        el.fill(p.phone)
                        result.fields_filled += 1
                        result.logs.append(f"Phone: {p.phone}")

                elif _has(meta, "linkedin"):
                    if not current:
                        el.fill(p.linkedin_url)
                        result.fields_filled += 1
                        result.logs.append(f"LinkedIn: {p.linkedin_url}")

                elif _has(meta, "github"):
                    if not current:
                        el.fill(p.github_url)
                        result.fields_filled += 1
                        result.logs.append(f"GitHub: {p.github_url}")

                elif _has(meta, "portfolio", "website", "personal site", "personal url"):
                    url_val = p.portfolio_url or p.linkedin_url
                    if not current and url_val:
                        el.fill(url_val)
                        result.fields_filled += 1
                        result.logs.append(f"Portfolio/Website: {url_val}")

                elif _has(meta, "location", "city", "current city", "where are you", "address"):
                    if not current:
                        el.fill(p.location)
                        result.fields_filled += 1
                        result.logs.append(f"Location: {p.location}")

                elif _has(meta, "salary", "compensation", "expected salary", "desired salary"):
                    if not current:
                        el.fill(prefs.expected_salary)
                        result.fields_filled += 1
                        result.logs.append(f"Salary: {prefs.expected_salary}")

                elif _has(meta, "notice", "available from", "start date", "availability"):
                    if not current:
                        el.fill(prefs.notice_period)
                        result.fields_filled += 1
                        result.logs.append(f"Notice period: {prefs.notice_period}")

                elif _has(meta, "cover letter", "cover_letter", "coverletter", "motivation", "why apply"):
                    if not current and cover_letter:
                        el.fill(cover_letter)
                        result.fields_filled += 1
                        source = "AI-generated" if self.ai_cover_letter else "static template"
                        result.logs.append(f"Cover letter filled ({source}).")

                elif tag == "textarea":
                    # Unknown textarea — try AI suggested answers, then cover letter
                    if not current:
                        answer = self._find_ai_answer(meta) or self._ask_ai(meta)
                        if answer:
                            el.fill(answer)
                            result.fields_filled += 1
                            result.logs.append(f"AI-answered: '{meta[:60]}'")
                        elif cover_letter:
                            el.fill(cover_letter)
                            result.fields_filled += 1
                            source = "AI-generated" if self.ai_cover_letter else "static template"
                            result.logs.append(f"Textarea filled with cover letter ({source}).")

            except Exception as exc:
                result.logs.append(f"Field error: {exc}")

    def _handle_select(self, target, el: ElementHandle, meta: str, result: FormFillResult) -> None:
        """Choose the correct option in a <select> dropdown."""
        p = self.profile.personal
        prefs = self.profile.work_preferences
        sa = self.profile.screening_answers

        try:
            # Get all option texts
            options = [
                (o.get_attribute("value") or "", o.inner_text().strip().lower())
                for o in target.query_selector_all(f"#{el.get_attribute('id')} option")
                if el.get_attribute('id')
            ]
            if not options:
                options = el.evaluate(
                    "el => Array.from(el.options).map(o => [o.value, o.text.toLowerCase()])"
                )

            def _pick(keywords: list[str]) -> Optional[str]:
                for val, text in options:
                    if any(kw in text for kw in keywords):
                        return val
                return None

            chosen = None

            if _has(meta, "authorized", "authorization", "eligible to work", "work authorization", "right to work", "legally"):
                auth = (sa.get("authorized_to_work") or "yes").lower()
                chosen = _pick(["yes", "authorized"] if auth == "yes" else ["no"])

            elif _has(meta, "sponsor", "sponsorship", "visa", "require visa"):
                req = (sa.get("require_sponsorship") or "no").lower()
                chosen = _pick(["no", "not required"] if req == "no" else ["yes"])

            elif _has(meta, "country", "nationality"):
                chosen = _pick(["united arab emirates", "uae", "india"])

            elif _has(meta, "notice", "available"):
                notice = prefs.notice_period.lower()
                if "immediate" in notice or "now" in notice:
                    chosen = _pick(["immediately", "0", "2 weeks", "1 month"])
                else:
                    chosen = _pick([notice])

            elif _has(meta, "experience", "years of experience", "yoe"):
                yoe = prefs.years_of_experience
                # find best matching bucket
                for val, text in options:
                    nums = re.findall(r"\d+", text)
                    if nums and any(int(n) >= yoe for n in nums):
                        chosen = val
                        break
                if not chosen and options:
                    chosen = options[-1][0]   # pick highest bucket

            elif _has(meta, "salary", "compensation"):
                chosen = _pick(["negotiable", "competitive", "open"]) or (options[0][0] if options else None)

            elif _has(meta, "gender"):
                chosen = _pick(["prefer not", "decline", "not specified"])

            elif _has(meta, "ethnicity", "race"):
                chosen = _pick(["prefer not", "decline", "not specified"])

            elif _has(meta, "disability"):
                chosen = _pick(["no", "not disabled", "prefer not"])

            elif _has(meta, "veteran"):
                chosen = _pick(["no", "not a veteran", "prefer not"])

            if chosen is not None:
                el.select_option(value=chosen)
                result.fields_filled += 1
                result.logs.append(f"Select '{meta[:40]}' → '{chosen}'")

        except Exception as e:
            result.logs.append(f"Select error for '{meta[:40]}': {e}")

    def _handle_number(self, el: ElementHandle, meta: str, prefs, result: FormFillResult) -> None:
        """Fill a number input (years of experience, salary, etc.)."""
        try:
            current = el.input_value()
            if current:
                return
            if _has(meta, "experience", "years"):
                el.fill(str(prefs.years_of_experience))
                result.fields_filled += 1
                result.logs.append(f"Years of experience: {prefs.years_of_experience}")
        except Exception as e:
            result.logs.append(f"Number field error: {e}")

    def _handle_checkbox(self, el: ElementHandle, meta: str, result: FormFillResult) -> None:
        """Auto-check 'I agree to terms' and similar consent checkboxes."""
        try:
            if _has(meta, "agree", "terms", "privacy", "consent", "authorize", "certify", "confirm"):
                if not el.is_checked():
                    el.check()
                    result.fields_filled += 1
                    result.logs.append(f"Checked consent checkbox: '{meta[:50]}'")
        except Exception as e:
            result.logs.append(f"Checkbox error: {e}")

    def _fill_radios_and_checkboxes(self, page: Page, result: FormFillResult) -> None:
        """
        Handle Yes/No radio groups for screening questions.
        Groups radios by name and selects the correct answer.
        """
        sa = self.profile.screening_answers
        prefs = self.profile.work_preferences

        try:
            # Find all visible radio inputs
            radios = page.query_selector_all("input[type='radio']")
            # Group by name
            groups: dict[str, list] = {}
            for r in radios:
                name = r.get_attribute("name") or ""
                if name:
                    groups.setdefault(name, []).append(r)

            for name, group in groups.items():
                # Build meta from first radio's label
                meta = self._meta(page, group[0])
                value_to_pick = None

                if _has(meta, "authorized", "eligible", "right to work", "legally", "authorization"):
                    auth = (sa.get("authorized_to_work") or "yes").lower()
                    value_to_pick = "yes" if auth == "yes" else "no"

                elif _has(meta, "sponsor", "sponsorship", "visa"):
                    req = (sa.get("require_sponsorship") or "no").lower()
                    value_to_pick = "yes" if req == "yes" else "no"

                elif _has(meta, "relocate", "relocation"):
                    rel = (sa.get("willing_to_relocate") or "yes").lower()
                    value_to_pick = "yes" if rel == "yes" else "no"

                if value_to_pick:
                    for radio in group:
                        val = (radio.get_attribute("value") or "").lower()
                        label_text = self._label_text(page, radio).lower()
                        if value_to_pick in val or value_to_pick in label_text:
                            if radio.is_visible():
                                radio.check()
                                result.fields_filled += 1
                                result.logs.append(
                                    f"Radio '{name}' → '{value_to_pick}'"
                                )
                            break

        except Exception as e:
            result.logs.append(f"Radio/checkbox scan error: {e}")

    # ── AI fallback for unknown fields ─────────────────────────────────────────

    def _find_ai_answer(self, meta: str) -> Optional[str]:
        """Try to match a form field to an AI-suggested answer from the tailored application."""
        if not self.ai_suggested_answers:
            return None
        field_mappings = {
            "why_interested":      ["why interested", "why this role", "why this position", "motivation", "why do you want"],
            "relevant_experience": ["relevant experience", "tell us about", "describe your experience", "background"],
            "greatest_strength":   ["greatest strength", "biggest strength", "what makes you", "key strength"],
        }
        meta_lower = meta.lower()
        for answer_key, keywords in field_mappings.items():
            if any(kw in meta_lower for kw in keywords):
                return self.ai_suggested_answers.get(answer_key)
        return None

    def _ask_ai(self, question_meta: str) -> Optional[str]:
        """Use Ollama to generate a contextual answer for an unknown textarea."""
        if not self.ai_client or not question_meta.strip():
            return None
        try:
            prompt = (
                f"You are filling out a job application form. "
                f"Answer this question in 2-3 sentences, professionally and concisely:\n\n"
                f"Question context: '{question_meta}'\n\n"
                f"Candidate profile:\n{self.profile.to_summary()}\n\n"
                f"Write only the answer, no labels or prefixes."
            )
            answer = self.ai_client.generate(prompt, temperature=0.5)
            return answer.strip() if answer else None
        except Exception:
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _meta(self, target, el: ElementHandle) -> str:
        parts = [
            el.get_attribute("name") or "",
            el.get_attribute("id") or "",
            el.get_attribute("placeholder") or "",
            el.get_attribute("aria-label") or "",
            el.get_attribute("autocomplete") or "",
            el.get_attribute("data-field-name") or "",
            self._label_text(target, el),
        ]
        return " ".join(parts).lower()

    @staticmethod
    def _label_text(target, el: ElementHandle) -> str:
        try:
            el_id = el.get_attribute("id")
            if el_id:
                lbl = target.query_selector(f"label[for='{el_id}']")
                if lbl:
                    return lbl.inner_text()
        except Exception:
            pass
        return ""

    def _click_apply_button(self, page: Page, logs: List[str]) -> bool:
        for sel in [
            "button:has-text('Apply now')",
            "a:has-text('Apply now')",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "[data-testid*='apply']",
            ".apply-button",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    logs.append(f"Clicked apply button: {sel}")
                    return True
            except Exception:
                continue
        return False

    def _submit_form(self, page: Page, logs: List[str]) -> bool:
        for sel in [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            "button:has-text('Send application')",
            "button:has-text('Send')",
            "button:has-text('Apply')",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(4000)
                    logs.append(f"Form submitted via: {sel}")
                    return True
            except Exception:
                continue
        logs.append("No visible submit button found.")
        return False

    @staticmethod
    def _captcha_present(page: Page) -> bool:
        try:
            try:
                body = page.content().lower()
            except Exception:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                body = page.content().lower()
            return any(k in body for k in [
                "g-recaptcha", "hcaptcha", "cf-turnstile",
                "enter your password", "sign in to continue"
            ])
        except Exception:
            return False


# ── Utility ────────────────────────────────────────────────────────────────────

def _has(text: str, *keywords: str) -> bool:
    return any(kw in text for kw in keywords)
