"""Pay and employment for the occupations in configs/career_occupations.toml.

Reads the federal Occupational Employment and Wage Statistics through the public
BLS data API, the channel BLS provides for automated clients; www.bls.gov itself
refuses them. Writes one Markdown document per occupation and an exact-SHA origin
manifest, and indexes nothing:

    python -m scripts.fetch_bls_wages
    python -m scripts.ingest --collection NAME --origin-manifest DIR/ingest-manifest.json DIR/documents/*
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time
import tomllib
from pathlib import Path

import httpx

from app.config import load_settings

API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SOURCE_URL = "https://www.bls.gov/oes/"
CONFIG = Path(__file__).resolve().parent.parent / "configs" / "career_occupations.toml"
SERIES_PER_REQUEST = 25
AREAS = {"United States": ("OEUN", "0000000"), "Atlanta metro area": ("OEUM", "0012060")}
MEASURES = {"01": "employment", "11": "wage10", "13": "median", "15": "wage90"}
ATTRIBUTION = ("U.S. Bureau of Labor Statistics, Occupational Employment and Wage Statistics. "
               "Public domain; cited as requested by BLS.")


def series_id(soc: str, prefix: str, area: str, measure: str) -> str:
    return f"{prefix}{area}000000{soc.replace('-', '')}{measure}"


def fetch(ids: list[str], client: httpx.Client) -> dict[str, dict]:
    latest = {}
    for start in range(0, len(ids), SERIES_PER_REQUEST):
        batch = ids[start:start + SERIES_PER_REQUEST]
        response = client.post(API, json={"seriesid": batch})
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS API refused the request: {body.get('message')}")
        for series in body["Results"]["series"]:
            if series["data"]:
                latest[series["seriesID"]] = series["data"][0]
        time.sleep(1)
    return latest


def _money(value: str | None) -> str:
    try:
        return f"${int(float(value)):,}"
    except (TypeError, ValueError):
        return "not published (BLS does not show a figure at this level)"


def _count(value: str | None) -> str:
    try:
        return f"{int(float(value)):,} jobs"
    except (TypeError, ValueError):
        return "not published"


def render(occupation: dict, values: dict[str, dict], year: str) -> str:
    summary = (f"Federal pay data for {occupation['title']} (SOC {occupation['soc']}), May {year}, "
               "from the U.S. Bureau of Labor Statistics Occupational Employment and Wage "
               "Statistics program. These figures cover all workers in the occupation, not new "
               "graduates or Emory alumni.")
    lines = [f"# {occupation['title']}: pay and employment", "", summary, "",
             "Related study paths: " + ", ".join(occupation["paths"]) + ".", ""]
    for area, (prefix, code) in AREAS.items():
        got = {name: (values.get(series_id(occupation["soc"], prefix, code, measure)) or {}).get("value")
               for measure, name in MEASURES.items()}
        lines += [f"## {area}", "",
                  f"- Median annual wage: {_money(got['median'])}",
                  f"- 10th percentile annual wage: {_money(got['wage10'])}",
                  f"- 90th percentile annual wage: {_money(got['wage90'])}",
                  f"- Employment: {_count(got['employment'])}", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    occupations = tomllib.loads(args.config.read_text(encoding="utf-8"))["occupation"]
    today = dt.datetime.now(dt.UTC).date().isoformat()
    output = args.output or load_settings().data_dir / "staging" / f"career-references-{today}"
    documents = output / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    ids = [series_id(item["soc"], prefix, code, measure) for item in occupations
           for prefix, code in AREAS.values() for measure in MEASURES]
    with httpx.Client(timeout=40) as client:
        values = fetch(ids, client)
    years = {point["year"] for point in values.values()}
    if len(years) != 1:
        raise RuntimeError(f"expected one survey year, got {sorted(years)}")
    year = years.pop()
    manifest = {}
    for item in occupations:
        if not any(series_id(item["soc"], *area, "13") in values or
                   series_id(item["soc"], *area, "01") in values for area in AREAS.values()):
            print(f"no data, skipped: {item['soc']} {item['title']}")
            continue
        name = f"bls-oews-{item['soc']}.md"
        content = render(item, values, year).encode("utf-8")
        (documents / name).write_bytes(content)
        manifest[f"documents/{name}"] = {
            "origin_key": f"career:bls:oews:{item['soc']}", "acquired_at": today,
            "source_url": SOURCE_URL, "content_sha256": hashlib.sha256(content).hexdigest(),
            "provenance": {
                "title": f"{item['title']}: BLS pay and employment", "source_category": "career_reference",
                "publisher": "U.S. Bureau of Labor Statistics", "source_url": SOURCE_URL,
                "edition": f"May {year}", "geography": "United States; Atlanta metro area",
                "occupation_id": item["soc"], "attribution": ATTRIBUTION,
                "document_roles": ["career_statistics"], "applicability_status": "reviewed_default",
                "applicability": {"geography": ["United States", "Atlanta metro area"]},
            },
        }
    (output / "ingest-manifest.json").write_text(
        json.dumps({"sources": manifest}, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{len(manifest)} occupation documents for May {year}: {output}")


if __name__ == "__main__":
    main()
