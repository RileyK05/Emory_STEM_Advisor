"""Controlled source provenance from a verified import manifest.

A trusted category (official catalog, department page, policy, curated career
reference) is assigned only by an explicit, verified importer: an entry in the
exact-SHA `--origin-manifest`, or a configured importer such as the atlas. A
document can never declare its own category, the upload endpoint cannot set one,
and no hostname or model heuristic infers one. Sources without a row read as
`user_supplied` (workspace uploads) or `unclassified` (shared, unverified).

Acquisition and publisher metadata live here, outside canonical document
identity: changing them never changes a source's content hash or ID.
"""

import re
from dataclasses import dataclass
from datetime import date

#: Categories a verified importer may assign. `user_supplied` and `unclassified`
#: are what unverified sources read as; nobody imports them as a claim.
TRUSTED_CATEGORIES = frozenset({
    "institutional_catalog", "institutional_department", "institutional_policy",
    "career_reference",
})
_TEXT_FIELDS = {"publisher": 300, "edition": 200, "geography": 200, "occupation_id": 100,
                "attribution": 1000}
_DATE_FIELDS = ("published_at", "acquired_at")
_ALLOWED = {"source_category", "source_url", *_TEXT_FIELDS, *_DATE_FIELDS}
_URL = re.compile(r"^https?://[^\s<>\"']{1,1990}$")


class ProvenanceError(ValueError):
    """A manifest provenance entry that is not a valid verified classification."""


@dataclass(frozen=True)
class SourceProvenance:
    source_category: str
    classification_origin: str = "verified_import"
    publisher: str | None = None
    source_url: str | None = None
    edition: str | None = None
    geography: str | None = None
    occupation_id: str | None = None
    published_at: date | None = None
    acquired_at: date | None = None
    attribution: str | None = None

    def row(self, source_id, content_hash: str) -> dict:
        return {"source_id": source_id, "content_hash": content_hash, **vars(self)}


def _date(value, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ProvenanceError(f"{field} must be an ISO date (YYYY-MM-DD)")


def provenance_from_manifest(entry: dict) -> SourceProvenance | None:
    """The validated provenance of one manifest entry, or None when it declares none.

    Entries without a `provenance` object stay valid (older manifests). The
    entry's top-level `source_url` and `acquired_at` fill in missing values, so
    the scraper's existing acquisition fields are not written twice.
    """
    raw = entry.get("provenance")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProvenanceError("provenance must be an object")
    unknown = set(raw) - _ALLOWED
    if unknown:
        raise ProvenanceError(f"unknown provenance fields: {sorted(unknown)}")
    category = raw.get("source_category")
    if category not in TRUSTED_CATEGORIES:
        raise ProvenanceError(
            f"source_category must be one of {sorted(TRUSTED_CATEGORIES)}")
    values = {"source_url": raw.get("source_url", entry.get("source_url")),
              "acquired_at": raw.get("acquired_at", entry.get("acquired_at"))}
    url = values["source_url"]
    if url is not None and (not isinstance(url, str) or not _URL.match(url)):
        raise ProvenanceError("source_url must be an http(s) URL")
    text = {}
    for field, limit in _TEXT_FIELDS.items():
        value = raw.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()
                                  or len(value) > limit):
            raise ProvenanceError(f"{field} must be a non-empty string of at most {limit} chars")
        text[field] = value.strip() if value is not None else None
    return SourceProvenance(
        source_category=category, source_url=url,
        published_at=_date(raw.get("published_at"), "published_at"),
        acquired_at=_date(values["acquired_at"], "acquired_at"), **text,
    )
