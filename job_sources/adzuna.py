"""
Adzuna connector.

Supported country codes (as of 2026):
  at, au, be, br, ca, ch, de, es, fr, gb, in, it, mx, nl, nz, pl, sg, us, za

UAE is NOT supported by Adzuna. Use Jooble for UAE jobs.
"""

import requests
from datetime import datetime
from typing import Optional, List

from job_sources.base import JobSourceConnector, RawJob, JobSourceError


class AdzunaConnector(JobSourceConnector):
    name = "adzuna"
    BASE_URL = "https://api.adzuna.com/v1/api/jobs"

    SUPPORTED_COUNTRIES = {
        "at", "au", "be", "br", "ca", "ch", "de", "es", "fr",
        "gb", "in", "it", "mx", "nl", "nz", "pl", "sg", "us", "za",
    }

    def __init__(self, app_id: str, app_key: str, timeout: int = 30):
        self.app_id = app_id
        self.app_key = app_key
        self.timeout = timeout

    def fetch_jobs(self, what: str, where: str, page: int = 1, country: str = "gb") -> List[RawJob]:
        country_code = country.lower().strip()

        if country_code not in self.SUPPORTED_COUNTRIES:
            # Gracefully skip unsupported country rather than crashing
            raise JobSourceError(
                self.name,
                f"Adzuna does not support country '{country}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_COUNTRIES))}. "
                "Tip: set DEFAULT_COUNTRY=gb in your .env to search UK jobs globally."
            )

        url = f"{self.BASE_URL}/{country_code}/search/{page}"
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": 20,
            "what": what,
            "where": where,
            "content-type": "application/json",
        }

        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise JobSourceError(self.name, f"Network error: {exc}")

        if resp.status_code != 200:
            raise JobSourceError(
                self.name,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
            )

        try:
            data = resp.json()
        except ValueError:
            raise JobSourceError(self.name, "Response was not valid JSON")

        return [self._to_raw(item) for item in data.get("results", [])]

    def _to_raw(self, item: dict) -> RawJob:
        return RawJob(
            source_name=self.name,
            source_job_id=str(item.get("id", "")),
            title=(item.get("title") or "").strip(),
            company=(item.get("company") or {}).get("display_name"),
            location=(item.get("location") or {}).get("display_name"),
            description=item.get("description"),
            url=item.get("redirect_url"),
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            currency=item.get("salary_currency") or "GBP",
            contract_type=item.get("contract_type") or "unknown",
            created_at_source=self._parse_date(item.get("created")),
            raw_payload=item,
        )

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
