"""Scrape Emory's public college catalog into atlas JSONL for `import_atlas`.

Usage (from backend/):
    python -m scripts.scrape_catalog [--subjects MATH CS ...] [--limit N]
    python -m scripts.scrape_catalog --offline     # rebuild JSONL from the raw cache only

Source: college.catalog.emory.edu (robots.txt: "Allow: /"). Course ids come
from its sitemap; each course page embeds the course record; requisite text
comes from the same public requirement-group endpoint the page itself calls.
The live Course Atlas (atlas.emory.edu) is gated and is not used.

Writes DATA_DIR/atlas/emory_college.jsonl and emory_college.failures.jsonl.
Every response is cached under DATA_DIR/atlas/raw/catalog/, so a re-run only
fetches what is missing. Requests identify the scraper, are spaced by --delay,
and honor Retry-After. The catalog sits behind an AWS WAF that answers fast
crawls with a CAPTCHA (seen after ~100 pages at 1 req/s); on a challenge the
run stops and keeps what it has. Never solve or route around the challenge.
"""

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from app.atlas.catalog import course_from_page, course_record, sitemap_paths, write_jsonl
from app.config import load_settings
from app.util.atomic import write_text_atomic

CATALOG = "https://college.catalog.emory.edu"
API = "https://app.coursedog.com/api/v1"
SCHOOL = "emory_peoplesoft_direct"
USER_AGENT = "Emory-STEM-Advisor-Scraper (+https://github.com/RileyK05/Emory_STEM_Advisor)"
GROUP_BATCH = 50
RETRIES = 3


class FetchError(RuntimeError):
    pass


class Blocked(FetchError):
    """The site answered with a bot challenge (AWS WAF CAPTCHA). Stop; don't work around it."""


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _retry_after(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdigit() else 5.0 * (attempt + 1)


def get(
    client: httpx.Client, url: str, *, sleep: Callable[[float], None] = time.sleep
) -> httpx.Response:
    last = ""
    for attempt in range(RETRIES):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            last = str(exc)
            sleep(3.0 * (attempt + 1))
            continue
        if response.headers.get("x-amzn-waf-action"):
            raise Blocked(f"bot challenge ({response.headers['x-amzn-waf-action']}) for {url}")
        if response.status_code == 429 or response.status_code >= 500:
            last = f"HTTP {response.status_code}"
            sleep(_retry_after(response, attempt))
            continue
        if response.status_code != 200:
            # 401/403/404: refused or missing. Don't retry around it.
            raise FetchError(f"HTTP {response.status_code} for {url}")
        return response
    raise FetchError(f"{url} failed after {RETRIES} attempts ({last})")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False))


def scrape(
    client: httpx.Client | None,
    *,
    raw_dir: Path,
    subjects: set[str] | None = None,
    limit: int | None = None,
    delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict], list[dict]]:
    """Returns (records, failures). `client=None` reads only from the cache."""
    failures: list[dict] = []

    sitemap_path = raw_dir / "sitemap.xml"
    if not sitemap_path.exists():
        if client is None:
            raise FetchError("sitemap not in cache; run once without --offline")
        sitemap_path.parent.mkdir(parents=True, exist_ok=True)
        sitemap_path.write_text(get(client, f"{CATALOG}/sitemap.xml", sleep=sleep).text,
                                encoding="utf-8")
        sleep(delay)
    course_ids = sitemap_paths(sitemap_path.read_text(encoding="utf-8"), "courses")
    if limit is not None:
        course_ids = course_ids[:limit]

    courses: list[dict] = []
    for index, course_id in enumerate(course_ids, 1):
        path = raw_dir / "courses" / f"{course_id}.json"
        try:
            if path.exists():
                course = _read_json(path)
            elif client is None:
                raise FetchError("not in cache")
            else:
                page = get(client, f"{CATALOG}/courses/{course_id}", sleep=sleep).text
                course = course_from_page(page)
                if course is None:
                    raise FetchError("page has no course record")
                _write_json(path, course)
                sleep(delay)
        except Blocked as exc:
            failures.append({"stage": "course", "id": course_id, "error": str(exc)})
            print(f"stopped at {index}/{len(course_ids)}: {exc}")
            print("cached pages are kept; re-run later (slower) to resume from here")
            break
        except (FetchError, ValueError) as exc:
            failures.append({"stage": "course", "id": course_id, "error": str(exc)})
            continue
        if subjects is None or str(course.get("subjectCode", "")).upper() in subjects:
            courses.append(course)
        if index % 100 == 0:
            print(f"  {index}/{len(course_ids)} course pages")

    groups_path = raw_dir / "requirement_groups.json"
    groups: dict[str, dict] = _read_json(groups_path) if groups_path.exists() else {}
    wanted = sorted({str(c["requirementGroup"]) for c in courses if c.get("requirementGroup")})
    missing = [g for g in wanted if g not in groups]
    if missing and client is not None:
        for start in range(0, len(missing), GROUP_BATCH):
            batch = missing[start:start + GROUP_BATCH]
            url = (f"{API}/{SCHOOL}/requirementGroups/{','.join(batch)}"
                   "?returnFields=code,catalogDisplayName,descriptionLong")
            try:
                groups.update(get(client, url, sleep=sleep).json().get("data") or {})
            except Blocked as exc:
                failures.append({"stage": "requirement_groups", "ids": batch,
                                 "error": str(exc)})
                print(f"stopped at requirement groups {start}/{len(missing)}: {exc}")
                print("cached groups are kept; re-run later (slower) to resume from here")
                _write_json(groups_path, groups)
                break
            except (FetchError, ValueError) as exc:
                failures.append({"stage": "requirement_groups", "ids": batch,
                                 "error": str(exc)})
            _write_json(groups_path, groups)
            sleep(delay)

    records: list[dict] = []
    seen: set[str] = set()
    for course in courses:
        record, reason = course_record(course, groups)
        if record is None:
            failures.append({"stage": "record", "id": course.get("courseGroupId"),
                             "code": course.get("code"), "error": reason})
        elif record["code"] in seen:
            failures.append({"stage": "record", "id": course.get("courseGroupId"),
                             "code": record["code"], "error": "duplicate code"})
        else:
            seen.add(record["code"])
            records.append(record)
    return records, failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subjects", nargs="+", default=None,
                        help="keep only these subject codes (default: all)")
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N course pages (for a trial run)")
    parser.add_argument("--delay", type=float, default=6.0,
                        help="seconds between live requests")
    parser.add_argument("--offline", action="store_true",
                        help="no network: build the JSONL from the raw cache only")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    atlas_dir = load_settings().data_dir / "atlas"
    out = args.out or atlas_dir / "emory_college.jsonl"
    failures_path = out.with_suffix(".failures.jsonl")
    subjects = {s.upper() for s in args.subjects} if args.subjects else None

    client = None if args.offline else make_client()
    try:
        records, failures = scrape(
            client, raw_dir=atlas_dir / "raw" / "catalog", subjects=subjects,
            limit=args.limit, delay=args.delay,
        )
    finally:
        if client is not None:
            client.close()

    write_jsonl(records, out)
    with failures_path.open("w", encoding="utf-8", newline="\n") as handle:
        for failure in failures:
            handle.write(json.dumps(failure) + "\n")
    print(f"wrote {len(records)} courses to {out}")
    if failures:
        print(f"{len(failures)} failures logged to {failures_path}")
    print(f"next: python -m scripts.import_atlas --collection default {out}")


if __name__ == "__main__":
    main()
