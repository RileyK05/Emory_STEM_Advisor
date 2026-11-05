"""Federal earnings and debt for Emory bachelor's graduates, by field of study.

Reads the U.S. Department of Education's College Scorecard field-of-study file and
writes one Markdown document and an exact-SHA origin manifest. Indexes nothing:

    python -m scripts.fetch_scorecard_fields [--zip LOCAL_FILE]
    python -m scripts.ingest --collection NAME --origin-manifest DIR/ingest-manifest.json DIR/documents/*
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx

from app.config import load_settings

DOWNLOAD = ("https://ed-public-download.scorecard.network/downloads/"
            "Most-Recent-Cohorts-Field-of-Study_10152026.zip")
EDITION = "Most Recent Cohorts file, October 2026"
SOURCE_URL = "https://collegescorecard.ed.gov/data/"
EMORY_UNITID = "139658"
BACHELORS = "3"
ATTRIBUTION = "U.S. Department of Education, College Scorecard. Public domain."
SUMMARY = (
    "What Emory University bachelor's graduates earn by major: federal salary and debt "
    "figures by field of study, "
    "from the U.S. Department of Education's College Scorecard ({edition}). Earnings are the "
    "median annual earnings of graduates who received federal student aid and were working "
    "and not enrolled, one year and four years after finishing. Students who never received "
    "federal aid are not counted, so these are not figures for all Emory graduates. Debt is "
    "the median federal loan debt of borrowers in the field, not counting Parent PLUS "
    "loans. Fields are federal study "
    "categories, not Emory majors, and small groups are withheld for privacy: a missing "
    "figure means the government did not publish one, not that it is zero. These figures "
    "describe past graduates and do not predict what any one student will earn.")


def rows(archive: Path) -> list[dict]:
    with zipfile.ZipFile(archive) as bundle:
        name = next(n for n in bundle.namelist() if n.lower().endswith(".csv"))
        with bundle.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            return [row for row in reader
                    if row["UNITID"] == EMORY_UNITID and row["CREDLEV"] == BACHELORS]


def _money(value: str | None) -> str | None:
    try:
        return f"${int(float(value)):,}"
    except (TypeError, ValueError):
        return None


def _title(field: str) -> str:
    return field.rstrip(".")


def render(fields: list[dict]) -> str:
    lines = ["# What Emory graduates earn by major: salary, pay and debt", "",
             SUMMARY.format(edition=EDITION), ""]
    published, withheld = [], []
    for row in sorted(fields, key=lambda item: _title(item["CIPDESC"])):
        year1, year4 = _money(row["EARN_MDN_1YR"]), _money(row["EARN_MDN_4YR"])
        if not (year1 or year4):
            withheld.append(_title(row["CIPDESC"]))
            continue
        parts = [f"one year after: {year1 or 'not published'}",
                 f"four years after: {year4 or 'not published'}"]
        if debt := _money(row["DEBT_ALL_STGP_ANY_MDN"]):
            parts.append(f"median federal debt: {debt}")
        published.append(f"- {_title(row['CIPDESC'])}: " + "; ".join(parts))
    lines += ["## Median earnings after graduation", "", *published, ""]
    if withheld:
        lines += ["## Fields with no published earnings", "",
                  "The government published no earnings for these fields, usually because too "
                  "few graduates were counted: " + "; ".join(withheld) + ".", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", type=Path, help="a saved copy of the field-of-study file")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    today = dt.datetime.now(dt.UTC).date().isoformat()
    output = args.output or load_settings().data_dir / "staging" / f"career-references-scorecard-{today}"
    documents = output / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    archive = args.zip
    if archive is None:
        archive = output / "field-of-study.zip"
        with httpx.Client(timeout=300, follow_redirects=True) as client:
            archive.write_bytes(client.get(DOWNLOAD).raise_for_status().content)
    fields = rows(archive)
    if not fields:
        raise RuntimeError(f"no bachelor's rows for UNITID {EMORY_UNITID}")
    name = "scorecard-emory-fields-of-study.md"
    content = render(fields).encode("utf-8")
    (documents / name).write_bytes(content)
    manifest = {f"documents/{name}": {
        "origin_key": "career:scorecard:emory-fields-of-study", "acquired_at": today,
        "source_url": SOURCE_URL, "content_sha256": hashlib.sha256(content).hexdigest(),
        "provenance": {
            "title": "What Emory Graduates Earn by Major: Salary and Debt (College Scorecard)",
            "source_category": "career_reference",
            "publisher": "U.S. Department of Education", "source_url": SOURCE_URL,
            "edition": EDITION, "geography": "United States", "attribution": ATTRIBUTION,
            "document_roles": ["career_statistics"], "applicability_status": "reviewed_default",
            "applicability": {"geography": ["United States"]},
        },
    }}
    (output / "ingest-manifest.json").write_text(
        json.dumps({"sources": manifest}, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{len(fields)} fields: {output}")


if __name__ == "__main__":
    main()
