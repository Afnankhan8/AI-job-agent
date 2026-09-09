"""
LinkedIn Jobs Connector.

Scrapes LinkedIn job listings using two strategies (in order):
  1. Playwright with the saved browser_profile/ session (logged in) — best results
  2. Plain HTTP request as fallback (no login required, may get fewer results)

To enable Strategy 1, run:
    python main.py --login-only
Once to establish the LinkedIn session. Subsequent runs will use saved cookies.
"""

import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from job_sources.base import JobSourceConnector, RawJob, JobSourceError


class LinkedInConnector(JobSourceConnector):
    name = "linkedin"
    BASE_URL = "https://www.linkedin.com/jobs/search"
    # Path to the persistent browser profile (relative to cwd — same as auto_apply.py)
    BROWSER_PROFILE = "browser_profile"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def fetch_jobs(self, what: str, where: str, page: int = 1) -> List[RawJob]:
        """
        Fetch LinkedIn jobs.
        Tries Playwright (authenticated session) first, falls back to HTTP.
        """
        profile_path = Path(self.BROWSER_PROFILE).resolve()

        if profile_path.exists():
            # Remove stale SingletonLock if present (left by a crashed browser)
            lock_file = profile_path / "SingletonLock"
            if lock_file.exists():
                try:
                    lock_file.unlink()
                except OSError:
                    pass

            try:
                return self._fetch_with_playwright(what, where, page, str(profile_path))
            except JobSourceError:
                raise   # Propagate auth wall errors to caller
            except Exception as e:
                err = str(e).lower()
                # If browser profile is locked by another process, skip Playwright quietly
                if "singleton" in err or "already in use" in err or "lock" in err:
                    pass  # fall through to HTTP
                else:
                    pass  # any other Playwright error → fall through to HTTP

        # Fallback: plain HTTP (works when not blocked)
        return self._fetch_with_http(what, where, page)

    # ── Playwright strategy (logged-in session) ────────────────────────────────

    def _fetch_with_playwright(
        self, what: str, where: str, page: int, profile_path: str
    ) -> List[RawJob]:
        """Use the saved browser session to scrape LinkedIn jobs."""
        from playwright.sync_api import sync_playwright

        start = (page - 1) * 25
        params = {
            "keywords": what,
            "location": where,
            "start": start,
            "f_TPR": "r2592000",    # Past month
            "sortBy": "DD",         # Most recent first
        }
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            pg = ctx.new_page()
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=30000)
                pg.wait_for_timeout(3000)

                # Detect auth wall
                if any(x in pg.url for x in ["/authwall", "/login", "/signup", "/uas/login"]):
                    raise JobSourceError(
                        self.name,
                        "LinkedIn session expired or not established. "
                        "Run: python main.py --login-only"
                    )

                # Scroll a bit to trigger lazy-loading of job cards
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                pg.wait_for_timeout(1500)

                content = pg.content()
            finally:
                pg.close()
                ctx.close()

        return self._parse_html(content, where)

    # ── HTTP fallback ──────────────────────────────────────────────────────────

    def _fetch_with_http(self, what: str, where: str, page: int) -> List[RawJob]:
        """Plain HTTP scrape of LinkedIn public job listings (no login required)."""
        start = (page - 1) * 25
        params = {
            "keywords": what,
            "location": where,
            "start": start,
            "f_TPR": "r2592000",    # Past month
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.linkedin.com/",
            "DNT": "1",
        }

        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise JobSourceError(self.name, f"Network error: {exc}")

        return self._parse_html(resp.text, where)

    # ── HTML parsing (shared) ──────────────────────────────────────────────────

    def _parse_html(self, html: str, where: str) -> List[RawJob]:
        """Parse LinkedIn job cards from HTML (works for both strategies)."""
        soup = BeautifulSoup(html, "html.parser")
        job_cards = soup.find_all("div", class_="base-card")

        # Authenticated pages use a different card class
        if not job_cards:
            job_cards = soup.find_all("li", class_=lambda c: c and "jobs-search-results__list-item" in c)
        if not job_cards:
            job_cards = soup.find_all("div", {"data-entity-urn": True})

        results: List[RawJob] = []
        for card in job_cards:
            try:
                # Link / URL
                link_tag = (
                    card.find("a", class_="base-card__full-link")
                    or card.find("a", class_=lambda c: c and "job-card-list__title" in c)
                    or card.find("a", href=lambda h: h and "/jobs/view/" in h)
                )
                if not link_tag:
                    continue

                job_url = link_tag.get("href", "").split("?")[0].strip()
                if not job_url:
                    continue

                job_id = job_url.split("-")[-1] if "-" in job_url else job_url

                # Title
                title_tag = (
                    card.find("h3", class_="base-search-card__title")
                    or card.find("a", class_=lambda c: c and "job-card-list__title" in c)
                    or link_tag
                )
                title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"

                # Company
                company_tag = (
                    card.find("h4", class_="base-search-card__subtitle")
                    or card.find("a", class_=lambda c: c and "job-card-container__company-name" in c)
                    or card.find("span", class_="job-card-container__primary-description")
                )
                company = company_tag.get_text(strip=True) if company_tag else "Unknown Company"

                # Location
                location_tag = (
                    card.find("span", class_="job-search-card__location")
                    or card.find("li", class_=lambda c: c and "job-card-container__metadata-item" in c)
                )
                location = location_tag.get_text(strip=True) if location_tag else where

                # Date
                date_tag = card.find("time", class_="job-search-card__listdate")
                created_at: Optional[datetime] = None
                if date_tag and date_tag.has_attr("datetime"):
                    try:
                        created_at = datetime.fromisoformat(date_tag["datetime"])
                    except ValueError:
                        pass
                if not created_at:
                    created_at = datetime.now(timezone.utc)

                results.append(RawJob(
                    source_name=self.name,
                    source_job_id=job_id,
                    title=title,
                    company=company,
                    location=location,
                    description="",   # Full description fetched separately by description_fetcher
                    url=job_url,
                    salary_min=None,
                    salary_max=None,
                    currency=None,
                    contract_type="Full-time",
                    created_at_source=created_at,
                    raw_payload={"url": job_url},
                ))
            except Exception:
                continue

        return results
