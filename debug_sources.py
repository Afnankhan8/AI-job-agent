"""
Diagnostic script — prints the RAW API response from each source,
bypassing our normalizer/connector abstractions entirely. Use this
when the pipeline reports 0 results and you need to see exactly
what the provider actually said (error message, empty jobs array,
totalCount, etc.) before assuming it's a code bug.

Run with: python debug_sources.py
"""

import json
import requests
from config.settings import SETTINGS

print("=" * 60)
print("JOOBLE RAW RESPONSE — MULTIPLE TEST COMBINATIONS")
print("=" * 60)

if SETTINGS.jooble_api_key:
    test_combos = [
        {"keywords": "AI Engineer", "location": "Dubai"},
        {"keywords": "AI Engineer", "location": "UAE"},
        {"keywords": "AI Engineer", "location": "United Arab Emirates"},
        {"keywords": "Engineer", "location": "Dubai"},
        {"keywords": "AI Engineer", "location": ""},
        {"keywords": "Software Engineer", "location": "Dubai"},
    ]
    url = f"https://jooble.org/api/{SETTINGS.jooble_api_key}"
    for combo in test_combos:
        resp = requests.post(url, json=combo, headers={"Content-Type": "application/json"})
        data = resp.json()
        count = data.get("totalCount", "?")
        print(f"keywords='{combo['keywords']}' location='{combo['location']}' -> totalCount: {count}")
else:
    print("JOOBLE_API_KEY not set in .env")

print("\n" + "=" * 60)
print("ADZUNA RAW RESPONSE")
print("=" * 60)

if SETTINGS.adzuna_app_id and SETTINGS.adzuna_app_key:
    url = f"https://api.adzuna.com/v1/api/jobs/{SETTINGS.default_country}/search/1"
    params = {
        "app_id": SETTINGS.adzuna_app_id,
        "app_key": SETTINGS.adzuna_app_key,
        "results_per_page": 10,
        "what": "AI Engineer",
        "where": "Dubai",
        "content-type": "application/json",
    }
    resp = requests.get(url, params=params)
    print(f"Status: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2)[:2000])
else:
    print("ADZUNA credentials not set in .env")