"""
description_fetcher.py — Scrapes full job descriptions in parallel.

Called after ingestion to hydrate jobs that have empty descriptions.
Works for LinkedIn and Jooble URLs.

Usage:
    from job_sources.description_fetcher import fetch_missing_descriptions
    fetched, failed = fetch_missing_descriptions(repo, max_workers=8, limit=200)
"""

import re
import time
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple

from bs4 import BeautifulSoup

from database.repository import JobRepository, JobRecord


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_TIMEOUT = 20


def _scrape_linkedin(url: str) -> str:
    """Scrape description from a LinkedIn job detail page."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Primary selector: show-more-less-html content
        desc_div = soup.find("div", class_=re.compile(r"show-more-less-html__markup"))
        if desc_div:
            return desc_div.get_text(separator="\n", strip=True)

        # Fallback: description container
        desc_div = soup.find("div", class_=re.compile(r"description__text"))
        if desc_div:
            return desc_div.get_text(separator="\n", strip=True)

        # Broader fallback — any job description section
        for cls in ["job-description", "jobs-description", "jobs-box__html-content"]:
            div = soup.find("div", class_=re.compile(cls))
            if div:
                return div.get_text(separator="\n", strip=True)

        return ""
    except Exception:
        return ""


def _scrape_jooble(url: str) -> str:
    """Scrape description from a Jooble job detail page."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Jooble wraps the description in article / div with data attributes
        for selector in [
            ("article", {"class": re.compile(r"vacancy")}),
            ("div", {"class": re.compile(r"description")}),
            ("div", {"itemprop": "description"}),
        ]:
            el = soup.find(*selector)
            if el:
                return el.get_text(separator="\n", strip=True)

        return ""
    except Exception:
        return ""


def _fetch_one(job: JobRecord) -> Tuple[int, str]:
    """Fetch description for a single job. Returns (job_id, description)."""
    url = job.url or ""
    # Tiny random delay to be polite and avoid rate-limiting
    time.sleep(random.uniform(0.3, 1.0))

    if "linkedin.com" in url:
        desc = _scrape_linkedin(url)
    elif "jooble.org" in url:
        desc = _scrape_jooble(url)
    else:
        # Generic fallback for other sources
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            soup = BeautifulSoup(resp.text, "html.parser")
            # Try common patterns
            for tag, attr in [
                ("div", {"class": re.compile(r"description", re.I)}),
                ("section", {"class": re.compile(r"description", re.I)}),
                ("article", {}),
            ]:
                el = soup.find(tag, attr)
                if el:
                    desc = el.get_text(separator="\n", strip=True)
                    break
            else:
                desc = ""
        except Exception:
            desc = ""

    return job.id, desc


def fetch_missing_descriptions(
    repo: JobRepository,
    max_workers: int = 8,
    limit: int = 200,
    verbose: bool = True,
) -> Tuple[int, int]:
    """
    Fetch and store descriptions for jobs that don't have one yet.

    Returns (fetched_count, failed_count).
    """
    jobs = repo.get_jobs_missing_description(limit=limit)
    if not jobs:
        if verbose:
            print("  [descriptions] All jobs already have descriptions.\n")
        return 0, 0

    if verbose:
        print(f"  [descriptions] Fetching descriptions for {len(jobs)} jobs "
              f"({max_workers} workers)...")

    fetched = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                job_id, desc = future.result()
                if desc and len(desc) > 50:
                    repo.update_description(job_id, desc)
                    fetched += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

    if verbose:
        print(f"  [descriptions] Done — {fetched} fetched, {failed} failed/empty.\n")

    return fetched, failed
