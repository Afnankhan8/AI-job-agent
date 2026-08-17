"""
URL Resolver.

Follows HTTP redirects on aggregator links (Jooble / Adzuna redirect URLs)
to discover the true destination application page.
"""

import requests
from typing import Tuple, List


def resolve_url(url: str, timeout: int = 15) -> Tuple[str, List[str]]:
    """
    Follow redirects and return (final_url, list_of_redirect_steps).
    If resolution fails, returns (original_url, [original_url]).
    """
    if not url:
        return "", []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    history = [url]
    try:
        resp = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout)
        final = resp.url
        for r in resp.history:
            if r.url not in history:
                history.append(r.url)
        if final not in history:
            history.append(final)
        return final, history
    except Exception:
        return url, history
