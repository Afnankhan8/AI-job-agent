"""
LinkedIn Jobs Connector.
Scrapes public LinkedIn job listings (no API key required).
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import List, Optional
import urllib.parse

from job_sources.base import JobSourceConnector, RawJob, JobSourceError


class LinkedInConnector(JobSourceConnector):
    name = "linkedin"
    BASE_URL = "https://www.linkedin.com/jobs/search"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def fetch_jobs(self, what: str, where: str, page: int = 1) -> List[RawJob]:
        # LinkedIn pagination: start=0, 25, 50, etc.
        start = (page - 1) * 25
        
        params = {
            "keywords": what,
            "location": where,
            "start": start,
            "f_TPR": "r2592000", # Past month
        }
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"
        
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise JobSourceError(self.name, f"Network error: {exc}")

        soup = BeautifulSoup(resp.text, 'html.parser')
        job_cards = soup.find_all('div', class_='base-card')
        
        results = []
        for card in job_cards:
            try:
                link_tag = card.find('a', class_='base-card__full-link')
                if not link_tag:
                    continue
                    
                job_url = link_tag.get('href', '').split('?')[0]
                job_id = job_url.split('-')[-1] if '-' in job_url else job_url
                
                title_tag = card.find('h3', class_='base-search-card__title')
                title = title_tag.text.strip() if title_tag else "Unknown Title"
                
                company_tag = card.find('h4', class_='base-search-card__subtitle')
                company = company_tag.text.strip() if company_tag else "Unknown Company"
                
                location_tag = card.find('span', class_='job-search-card__location')
                location = location_tag.text.strip() if location_tag else where
                
                date_tag = card.find('time', class_='job-search-card__listdate')
                created_at = None
                if date_tag and date_tag.has_attr('datetime'):
                    try:
                        created_at = datetime.fromisoformat(date_tag['datetime'])
                    except ValueError:
                        pass
                
                if not created_at:
                    created_at = datetime.now(timezone.utc)

                raw = RawJob(
                    source_name=self.name,
                    source_job_id=job_id,
                    title=title,
                    company=company,
                    location=location,
                    description="", # Description requires separate page load, we skip to save time
                    url=job_url,
                    salary_min=None,
                    salary_max=None,
                    currency=None,
                    contract_type="Full-time",
                    created_at_source=created_at,
                    raw_payload={"url": job_url}
                )
                results.append(raw)
            except Exception:
                continue

        return results
