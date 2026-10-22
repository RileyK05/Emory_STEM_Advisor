"""Controlled source provenance from a verified import manifest.

A trusted category (official catalog, department page, policy, institutional
publication, curated career reference) is assigned only by an explicit, verified
importer: an entry in the exact-SHA `--origin-manifest`, or a configured importer
such as the atlas. A document can never declare its own category, the upload
endpoint cannot set one, and no hostname or model heuristic infers one. Sources without a row read as
`user_supplied` (workspace uploads) or `unclassified` (shared, unverified).

Acquisition and publisher metadata live here, outside canonical document
identity: changing them never changes a source's content hash or ID.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from psycopg.types.json import Jsonb

from app.util.digest import canonical_json_bytes

#: Categories a verified importer may assign. `user_supplied` and `unclassified`
#: are what unverified sources read as; nobody imports them as a claim.
TRUSTED_CATEGORIES = frozenset({
    "institutional_catalog", "institutional_department", "institutional_policy",
    "institutional_publication", "career_reference",
})
_TEXT_FIELDS = {"publisher": 300, "edition": 200, "geography": 200, "occupation_id": 100,
                "attribution": 1000, "title": 300, "institution": 200, "issuing_unit": 200}
_DATE_FIELDS = ("published_at", "acquired_at")
_ALLOWED = {"source_category", "source_url", "document_roles", "applicability",
            "applicability_status", *_TEXT_FIELDS, *_DATE_FIELDS}
_URL = re.compile(r"^https?://[^\s<>\"']{1,1990}$")
_DIMENSION = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_APPLICABILITY_STATUSES = ("reviewed_default", "historical", "unknown")
_MAX_METADATA_BYTES = 8192


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
    title: str | None = None
    institution: str | None = None
    issuing_unit: str | None = None
    document_roles: tuple[str, ...] = ()
    applicability: dict[str, tuple[str, ...]] = field(default_factory=dict)
    applicability_status: Literal["reviewed_default", "historical", "unknown"] = "unknown"
    lifecycle_status: Literal["staged", "active", "deprecated", "archived"] = "active"

    def row(self, source_id, content_hash: str) -> dict:
        return {"source_id": source_id, "content_hash": content_hash, **vars(self),
                "document_roles": Jsonb(sorted(set(self.document_roles))),
                "applicability": Jsonb({key: sorted(set(values))
                                        for key, values in self.applicability.items()}),
                "lifecycle_status": self.lifecycle_status}


def _strings(value, field: str, *, limit: int) -> tuple[str, ...]:
    """Bound curator labels without closing their vocabulary."""
    if not isinstance(value, list) or len(value) > 20:
        raise ProvenanceError(f"{field} must be a list of at most 20 strings")
    labels = []
    for label in value:
        if not isinstance(label, str) or not label.strip() or len(label) > limit:
            raise ProvenanceError(
                f"{field} values must be non-empty strings of at most {limit} chars")
        labels.append(label.strip())
    # Labels are sets for discovery. Stable ordering makes equivalent imports
    # retain their metadata revision and avoids needless evidence invalidation.
    return tuple(sorted(set(labels)))


def _applicability(value) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict) or len(value) > 20:
        raise ProvenanceError("applicability must be an object with at most 20 dimensions")
    result = {}
    for dimension, labels in value.items():
        if (not isinstance(dimension, str) or len(dimension) > 64
                or not _DIMENSION.fullmatch(dimension)):
            raise ProvenanceError("applicability dimensions must be snake_case (at most 64 chars)")
        result[dimension] = _strings(labels, f"applicability.{dimension}", limit=200)
    return result


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
    if not isinstance(category, str) or category not in TRUSTED_CATEGORIES:
        raise ProvenanceError(
            f"source_category must be one of {sorted(TRUSTED_CATEGORIES)}")
    values = {"source_url": raw.get("source_url", entry.get("source_url")),
              "acquired_at": raw.get("acquired_at", entry.get("acquired_at"))}
    url = values["source_url"]
    if url is not None and (not isinstance(url, str) or not _URL.match(url)):
        raise ProvenanceError("source_url must be an http(s) URL")
    text = {}
    for field_name, limit in _TEXT_FIELDS.items():
        value = raw.get(field_name)
        if value is not None and (not isinstance(value, str) or not value.strip()
                                  or len(value) > limit):
            raise ProvenanceError(f"{field_name} must be a non-empty string of at most {limit} chars")
        text[field_name] = value.strip() if value is not None else None
    status = raw.get("applicability_status", "unknown")
    if status not in _APPLICABILITY_STATUSES:
        raise ProvenanceError(f"applicability_status must be one of {_APPLICABILITY_STATUSES}")
    provenance = SourceProvenance(
        source_category=category, source_url=url,
        published_at=_date(raw.get("published_at"), "published_at"),
        acquired_at=_date(values["acquired_at"], "acquired_at"),
        document_roles=_strings(raw.get("document_roles", []), "document_roles", limit=100),
        applicability=_applicability(raw.get("applicability", {})),
        applicability_status=status, **text,
    )
    # Count normalized UTF-8 JSON, including acquisition fallbacks, rather than
    # Python characters. Large metadata otherwise crowds exact evidence out of
    # research prompts or exceeds a read tool's output bound.
    metadata = {key: value.isoformat() if isinstance(value, date) else value
                for key, value in vars(provenance).items() if key != "classification_origin"}
    if len(canonical_json_bytes(metadata)) > _MAX_METADATA_BYTES:
        raise ProvenanceError(f"provenance metadata exceeds {_MAX_METADATA_BYTES} UTF-8 bytes")
    return provenance


def validate_curator_proposal(raw: dict) -> dict:
    """Validate a curator/admin source review proposal using verified importer rules."""
    if not isinstance(raw, dict):
        raise ProvenanceError("proposal must be an object")

    def _get(snake: str, camel: str, default=None):
        if snake in raw and raw[snake] is not None:
            return raw[snake]
        if camel in raw and raw[camel] is not None:
            return raw[camel]
        return default

    category = _get("source_category", "sourceCategory")
    if not isinstance(category, str) or category not in (TRUSTED_CATEGORIES | {"user_supplied", "unclassified"}):
        raise ProvenanceError(
            f"source_category must be one of {sorted(TRUSTED_CATEGORIES | {'user_supplied', 'unclassified'})}"
        )

    url = _get("source_url", "sourceUrl")
    if url is not None and (not isinstance(url, str) or not _URL.match(url)):
        raise ProvenanceError("source_url must be an http(s) URL")

    camel_text = {
        "occupation_id": "occupationId",
        "issuing_unit": "issuingUnit",
    }
    text = {}
    for field_name, limit in _TEXT_FIELDS.items():
        camel_name = camel_text.get(field_name, field_name)
        val = _get(field_name, camel_name)
        if val is not None and (not isinstance(val, str) or not val.strip() or len(val) > limit):
            raise ProvenanceError(f"{field_name} must be a non-empty string of at most {limit} chars")
        text[field_name] = val.strip() if val is not None else None

    status = _get("applicability_status", "applicabilityStatus", "unknown")
    if status not in _APPLICABILITY_STATUSES:
        raise ProvenanceError(f"applicability_status must be one of {_APPLICABILITY_STATUSES}")

    lifecycle_status = _get("lifecycle_status", "lifecycleStatus", "active")
    if lifecycle_status not in ("staged", "active", "deprecated", "archived"):
        raise ProvenanceError("lifecycle_status must be one of ('staged', 'active', 'deprecated', 'archived')")

    roles = _strings(_get("document_roles", "documentRoles", []), "document_roles", limit=100)
    applicability = _applicability(_get("applicability", "applicability", {}))
    published_at = _date(_get("published_at", "publishedAt"), "published_at")
    acquired_at = _date(_get("acquired_at", "acquiredAt"), "acquired_at")

    replacement_intent = _get("replacement_intent", "replacementIntent")
    if replacement_intent is not None and not isinstance(replacement_intent, str):
        raise ProvenanceError("replacement_intent must be a string")
    review_notes = _get("review_notes", "reviewNotes")
    if review_notes is not None and not isinstance(review_notes, str):
        raise ProvenanceError("review_notes must be a string")

    return {
        "source_category": category,
        "classification_origin": "verified_curator",
        "publisher": text.get("publisher"),
        "source_url": url,
        "edition": text.get("edition"),
        "geography": text.get("geography"),
        "occupation_id": text.get("occupation_id"),
        "published_at": published_at,
        "acquired_at": acquired_at,
        "attribution": text.get("attribution"),
        "title": text.get("title"),
        "institution": text.get("institution"),
        "issuing_unit": text.get("issuing_unit"),
        "document_roles": roles,
        "applicability": applicability,
        "applicability_status": status,
        "lifecycle_status": lifecycle_status,
        "replacement_intent": replacement_intent,
        "review_notes": review_notes,
    }
