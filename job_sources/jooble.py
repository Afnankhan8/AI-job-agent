"""
Jooble connector.

Jooble API: POST https://jooble.org/api/{api_key}
Body: { keywords, location, page, ResultOnPage }
Response: { totalCount, jobs: [...] }

Jooble works well for UAE / Middle East searches.
"""

import re
import requests
from datetime import datetime
from typing import Optional, List

from job_sources.base import JobSourceConnector, RawJob, JobSourceError


class JoobleConnector(JobSourceConnector):
    name = "jooble"
    BASE_URL = "https://jooble.org/api"

    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout

    def fetch_jobs(self, what: str, where: str, page: int = 1) -> List[RawJob]:
        url = f"{self.BASE_URL}/{self.api_key}"
        payload = {
            "keywords": what,
            "location": where,
            "page": str(page),
            "ResultOnPage": "20",
        }

        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
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

        jobs = data.get("jobs", [])
        return [self._to_raw(item) for item in jobs]

    def _to_raw(self, item: dict) -> RawJob:
        salary_min, salary_max = self._parse_salary(item.get("salary"))
        return RawJob(
            source_name=self.name,
            source_job_id=str(item.get("id") or item.get("link", "")),
            title=(item.get("title") or "").strip(),
            company=item.get("company"),
            location=item.get("location"),
            description=item.get("snippet"),
            url=item.get("link"),
            salary_min=salary_min,
            salary_max=salary_max,
            currency=None,
            contract_type=item.get("type") or "unknown",
            created_at_source=self._parse_date(item.get("updated")),
            raw_payload=item,
        )

    @staticmethod
    def _parse_salary(raw: Optional[str]):
        if not raw:
            return None, None
        numbers = re.findall(r"[\d,]+(?:\.\d+)?", raw)
        cleaned = [float(n.replace(",", "")) for n in numbers if n.replace(",", "").replace(".", "").isdigit()]
        if len(cleaned) >= 2:
            return min(cleaned), max(cleaned)
        if len(cleaned) == 1:
            return cleaned[0], cleaned[0]
        return None, None

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
