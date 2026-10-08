"""
Emory Atlas course scraper (FOSE API).

Flow:
  1. For each subject, POST route=search  -> section-level rows
  2. Group sections by course code (+ collect their CRNs)
  3. For each course, POST route=details  -> description + registration_restrictions (prereqs)
  4. Save raw JSON (so you never re-scrape while iterating) and a cleaned courses.json

Usage:
  python atlas_scraper.py                      # default subjects, term 5269
  python atlas_scraper.py --subjects MATH CS   # specific subjects
  python atlas_scraper.py --srcdb 5269         # different term code
"""
import argparse
import json
import re
import time
from pathlib import Path

import requests

URL = "https://atlas.emory.edu/api/"
HEADERS = {
    "User-Agent": "STEM-Advisor-Bot (contact: you@emory.edu)",  # <- put your real contact
    "Accept": "application/json",
    "Content-Type": "application/json",
}
DELAY = 0.6  # seconds between requests; be polite

# Starter STEM list. Edit freely (check codes against the atlas subject dropdown).
DEFAULT_SUBJECTS = ["MATH", "CS", "QTM", "PHYS", "CHEM", "BIOL", "NEUR", "ENVS", "ISOM"]

# How to filter by subject. Keyword search is what you've confirmed works.
# If the atlas UI sends a dedicated subject field when you use its Subject filter,
# change SEARCH_FIELD to that field name (check the Payload tab) for cleaner results.
SEARCH_FIELD = "keyword"

CODE_RE = re.compile(r"\b([A-Z]{2,}(?:_[A-Z]+)?)\s(\d{3}[A-Z]{0,2})\b")

RAW_DIR = Path("raw")
session = requests.Session()
session.headers.update(HEADERS)


def post(route, body, extra_params=None, retries=3):
    params = {"page": "fose", "route": route}
    if extra_params:
        params.update(extra_params)
    for attempt in range(retries):
        try:
            r = session.post(URL, params=params, data=json.dumps(body), timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Failed: {route} {body}")


def search(subject, srcdb):
    body = {
        "other": {"srcdb": srcdb},
        "criteria": [{"field": SEARCH_FIELD, "value": subject}],
    }
    extra = {"keyword": subject} if SEARCH_FIELD == "keyword" else None
    return post("search", body, extra)


def details(code, crns, srcdb):
    body = {
        "group": f"code:{code}",
        "key": "",
        "srcdb": srcdb,
        "matched": "crn:" + ",".join(crns),
    }
    return post("details", body)


def group_courses(results, subject_filter=None):
    """Collapse section rows into {code: {title, crns}}."""
    courses = {}
    for s in results:
        code = s["code"]
        # keyword search also returns cross-lists/other subjects; optionally keep only this subject
        if subject_filter and not code.startswith(subject_filter):
            continue
        c = courses.setdefault(code, {"code": code, "title": s["title"], "crns": []})
        if s["crn"] not in c["crns"]:
            c["crns"].append(s["crn"])
    return courses


def extract_codes(text):
    """Course codes mentioned in a requirement string, e.g. 'MATH 111', 'ECON_OX 101'."""
    return sorted({f"{a} {b}" for a, b in CODE_RE.findall(text or "")})


def clean(detail):
    prereq = detail.get("registration_restrictions", "") or ""
    return {
        "code": detail.get("code"),
        "title": detail.get("title"),
        "credits": detail.get("credit_hours_options") or detail.get("hours_html"),
        "description": detail.get("description"),
        "prereq_text": prereq,
        "prereq_codes": extract_codes(prereq),
        "consent_required": detail.get("consent_required"),
        "typically_offered": detail.get("crse_typoff_html"),
        "gen_ed": detail.get("clss_assoc_rqmnt_designt_html"),
        "grading": detail.get("gmods"),
        "cross_listed": detail.get("xlist"),
        "notes": detail.get("clssnotes"),
        "srcdb": detail.get("srcdb"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", nargs="+", default=DEFAULT_SUBJECTS)
    ap.add_argument("--srcdb", default="5269")
    ap.add_argument("--out", default="courses.json")
    args = ap.parse_args()

    (RAW_DIR / "search").mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "details").mkdir(parents=True, exist_ok=True)

    all_courses = {}
    for subj in args.subjects:
        sp = RAW_DIR / "search" / f"{args.srcdb}_{subj}.json"
        if sp.exists():
            data = json.loads(sp.read_text())
        else:
            data = search(subj, args.srcdb)
            sp.write_text(json.dumps(data))
            time.sleep(DELAY)
        found = group_courses(data.get("results", []), subject_filter=subj)
        print(f"{subj}: {data.get('count', '?')} sections -> {len(found)} courses")
        all_courses.update(found)

    cleaned = []
    for i, (code, c) in enumerate(sorted(all_courses.items()), 1):
        safe = code.replace(" ", "_")
        dp = RAW_DIR / "details" / f"{args.srcdb}_{safe}.json"
        try:
            if dp.exists():
                d = json.loads(dp.read_text())
            else:
                d = details(code, c["crns"], args.srcdb)
                dp.write_text(json.dumps(d))
                time.sleep(DELAY)
        except Exception as e:
            print(f"  !! {code}: {e}")
            continue
        cleaned.append(clean(d))
        if i % 25 == 0:
            print(f"  {i}/{len(all_courses)} details fetched")

    Path(args.out).write_text(json.dumps(cleaned, indent=2))
    print(f"Saved {len(cleaned)} courses to {args.out}")


if __name__ == "__main__":
    main()