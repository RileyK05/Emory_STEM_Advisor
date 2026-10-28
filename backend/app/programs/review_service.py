"""Service layer for program requirement raw inspection, rule drafts, review, and release.

Enforces zero-LLM deterministic extraction, exact source binding validation,
100% clause coverage, and immutable release versioning.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import queries
from app.programs.rules import (
    RuleValidationError,
    compute_rules_digest,
    generate_default_rules_tree,
    validate_rules_tree,
)
from app.util.json import loose_jsonb

logger = logging.getLogger(__name__)


def _evidence_changed(program):
    return (not program["is_active"] or not program["source_is_current"]
            or program["source_status"] != "indexed"
            or program["source_lifecycle_status"] in {"staged", "deprecated", "archived"}
            or program["source_index_revision"] != program["current_source_index_revision"]
            or program["source_sha256"] != program["current_source_sha256"])


def _current_program(conn, program_id, release=None):
    program = conn.execute(queries.get("get_program_inspection_detail"), {
        "program_id": program_id,
    }).fetchone()
    if not program:
        raise HTTPException(404, "program not found")
    if _evidence_changed(program):
        raise HTTPException(409, "stale evidence prevents release: program/source revision changed")
    locked = conn.execute(queries.get("lock_program_source"), {
        "source_id": program["source_id"], "index_revision": program["source_index_revision"],
        "file_hash": program["source_sha256"],
    }).fetchone()
    if not locked:
        raise HTTPException(409, "stale evidence prevents release: source publication changed")
    # The source lock also fences provenance writers; re-read after acquiring it.
    program = conn.execute(queries.get("get_program_inspection_detail"), {
        "program_id": program_id,
    }).fetchone()
    if _evidence_changed(program):
        raise HTTPException(409, "stale evidence prevents release: source metadata changed")
    if release is not None and (
            release["source_id"] != program["source_id"]
            or release["source_index_revision"] != program["source_index_revision"]
            or release["source_sha256"] != program["source_sha256"]
            or release["bound_program_digest"] != program["parsed_content_digest"]
            or release["source_metadata_revision"] != (program["source_metadata_revision"] or 0)):
        raise HTTPException(409, "stale evidence prevents release: reviewed projection or metadata changed")
    return program


def _locked_release(conn, release_id, expected_program_id=None):
    release = conn.execute(queries.get("get_program_rule_release"), {
        "release_id": release_id,
    }).fetchone()
    if not release or (expected_program_id is not None and release["program_id"] != expected_program_id):
        raise HTTPException(404, "program rule release not found")
    conn.execute(queries.get("lock_program_rule_review"), {"program_id": release["program_id"]})
    return conn.execute(queries.get("lock_program_rule_release"), {"release_id": release_id}).fetchone()


def _require_identity_evidence(conn, program):
    """Require exact current-source support for every declared identity field.

    Requirement clauses may remain unresolved for human review, but a release
    cannot freeze a title or degree inferred from somewhere other than its
    indexed source header.
    """
    rows = conn.execute(queries.get("list_program_identity_evidence"), {
        "program_id": program["program_id"],
    }).fetchall()
    expected = {"title": program["title"]}
    if program["degree"]:
        expected["degree"] = program["degree"]
    grouped = {field: [] for field in expected}
    for row in rows:
        field = row["field"]
        if field not in expected:
            continue
        start, end = row["span_start"], row["span_end"]
        chunk_text = row["chunk_text"]
        if (row["source_id"] != program["source_id"]
                or row["chunk_source_id"] != program["source_id"]
                or row["source_index_revision"] != program["source_index_revision"]
                or row["chunk_start_char"] is None
                or start is None or end is None or start < 0 or end <= start
                or end > len(chunk_text)):
            raise HTTPException(409, f"program identity evidence is invalid for: {field}")
        span = chunk_text[start:end]
        if hashlib.sha256(span.encode("utf-8")).hexdigest() != row["text_digest"]:
            raise HTTPException(409, f"program identity evidence is invalid for: {field}")
        grouped[field].append((row["ordinal"], row["chunk_start_char"] + start, span))
    supported = set()
    for field, pieces in grouped.items():
        if not pieces:
            continue
        pieces.sort(key=lambda piece: piece[0])
        if [piece[0] for piece in pieces] != list(range(len(pieces))):
            continue
        assembled = pieces[0][2]
        previous_end = pieces[0][1] + len(pieces[0][2])
        valid = True
        for _ordinal, source_start, span in pieces[1:]:
            gap = source_start - previous_end
            if gap < 0 or gap > 2:
                valid = False
                break
            assembled += "\n" * gap + span
            previous_end = source_start + len(span)
        if not valid:
            continue
        if field == "title":
            matches = " ".join(expected[field].split()).casefold() == " ".join(assembled.split()).casefold()
        else:
            header = re.match(r"^D\w*GREE\s+A\w*RDED(?![A-Z])\s*", assembled, re.IGNORECASE)
            matches = bool(header and assembled[header.end():].strip().casefold()
                           == expected[field].strip().casefold())
        if matches:
            supported.add(field)
    missing = set(expected) - supported
    if missing:
        names = ", ".join(sorted(missing))
        raise HTTPException(
            409,
            f"program identity evidence is incomplete or invalid for: {names}",
        )


class ProgramReviewService:
    @staticmethod
    def get_program_inspection(conn, program_id: UUID) -> dict:
        program = conn.execute(
            queries.get("get_program_inspection_detail"), {"program_id": program_id}
        ).fetchone()
        if not program:
            raise HTTPException(404, f"program {program_id} not found")

        items = conn.execute(
            queries.get("list_program_items_for_inspection"), {"program_id": program_id}
        ).fetchall()

        releases = conn.execute(
            queries.get("list_rule_releases_for_program"), {"program_id": program_id}
        ).fetchall()

        current_release = None
        if program["current_rule_release_id"]:
            current_release_row = conn.execute(
                queries.get("get_program_rule_release"),
                {"release_id": program["current_rule_release_id"]},
            ).fetchone()
            if current_release_row:
                current_release = dict(current_release_row)

        return {
            "program": dict(program),
            "items": [dict(it) for it in items],
            "releases": [dict(r) for r in releases],
            "currentRelease": current_release,
        }

    @staticmethod
    def create_rule_draft(
        conn,
        program_id: UUID,
        created_by_user_id: UUID | None,
        rules_tree: dict | None = None,
        notes: str = "",
    ) -> dict:
        conn.execute(queries.get("lock_program_rule_review"), {"program_id": program_id})
        program = _current_program(conn, program_id)
        if not program:
            raise HTTPException(404, f"program {program_id} not found")

        if program["binding_status"] not in ("bound", "evidence_unresolved"):
            raise HTTPException(
                400,
                f"program must be bound to a verified source before creating a rule release (currently '{program['binding_status']}')",
            )

        if not program["is_active"] or not program["source_id"] or not program["source_is_current"]:
            raise HTTPException(
                409,
                "stale evidence prevents release: bound source is missing or no longer current",
            )

        _require_identity_evidence(conn, program)

        raw_items = conn.execute(
            queries.get("list_program_items_for_inspection"), {"program_id": program_id}
        ).fetchall()
        item_dicts = [dict(it) for it in raw_items]

        if rules_tree is None:
            rules_tree = generate_default_rules_tree(dict(program), item_dicts)

        try:
            validated_tree, coverage_summary = validate_rules_tree(rules_tree, item_dicts)
        except RuleValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

        rules_digest = compute_rules_digest(validated_tree)

        max_ver_row = conn.execute(
            queries.get("get_latest_rule_release_version"), {"program_id": program_id}
        ).fetchone()
        next_version = (max_ver_row["max_version"] if max_ver_row else 0) + 1

        release_id = uuid4()
        params = {
            "release_id": release_id,
            "program_id": program_id,
            "collection_id": program["collection_id"],
            "source_id": program["source_id"],
            "source_index_revision": program["source_index_revision"],
            "source_sha256": program["source_sha256"],
            "release_version": next_version,
            "status": "draft",
            "rules_tree": loose_jsonb(validated_tree),
            "rules_digest": rules_digest,
            "coverage_summary": loose_jsonb(coverage_summary),
            "created_by_user_id": created_by_user_id,
            "notes": notes,
            "bound_program_digest": program["parsed_content_digest"],
            "source_metadata_revision": program["source_metadata_revision"] or 0,
        }

        created = conn.execute(queries.get("insert_program_rule_release"), params).fetchone()

        # Record review audit event
        conn.execute(
            queries.get("insert_program_rule_review"),
            {
                "review_id": uuid4(),
                "release_id": release_id,
                "reviewer_user_id": created_by_user_id,
                "action": "submitted",
                "review_notes": f"Draft version {next_version} created: {notes}",
            },
        )
        conn.commit()
        return dict(created)

    @staticmethod
    def approve_rule_release(
        conn,
        release_id: UUID,
        reviewer_user_id: UUID | None,
        notes: str = "",
        *, expected_program_id: UUID | None = None,
    ) -> dict:
        release = _locked_release(conn, release_id, expected_program_id)
        program = _current_program(conn, release["program_id"], release)
        _require_identity_evidence(conn, program)
        if release["status"] not in ("draft", "reviewing"):
            raise HTTPException(
                409, f"cannot approve release with status '{release['status']}'"
            )

        now = datetime.now(UTC)
        updated = conn.execute(
            queries.get("update_program_rule_release_status"),
            {
                "release_id": release_id,
                "status": "approved",
                "approved_by_user_id": reviewer_user_id,
                "approved_at": now,
            },
        ).fetchone()

        conn.execute(
            queries.get("insert_program_rule_review"),
            {
                "review_id": uuid4(),
                "release_id": release_id,
                "reviewer_user_id": reviewer_user_id,
                "action": "approved",
                "review_notes": notes,
            },
        )
        conn.commit()
        return dict(updated)

    @staticmethod
    def publish_rule_release(
        conn,
        release_id: UUID,
        reviewer_user_id: UUID | None,
        notes: str = "",
        *, expected_program_id: UUID | None = None,
    ) -> dict:
        release = _locked_release(conn, release_id, expected_program_id)
        program = _current_program(conn, release["program_id"], release)
        _require_identity_evidence(conn, program)
        if release["status"] != "approved":
            raise HTTPException(
                409, f"cannot publish release with status '{release['status']}'"
            )

        program_id = release["program_id"]
        # Archive any currently published release for this program
        all_releases = conn.execute(
            queries.get("list_rule_releases_for_program"), {"program_id": program_id}
        ).fetchall()
        for r in all_releases:
            if r["release_id"] != release_id and r["status"] == "published":
                conn.execute(
                    queries.get("update_program_rule_release_status"),
                    {
                        "release_id": r["release_id"],
                        "status": "archived",
                        "approved_by_user_id": r["approved_by_user_id"],
                        "approved_at": r["approved_at"],
                    },
                )

        published = conn.execute(
            queries.get("update_program_rule_release_status"),
            {
                "release_id": release_id,
                "status": "published",
                "approved_by_user_id": release["approved_by_user_id"],
                "approved_at": release["approved_at"],
            },
        ).fetchone()

        # Update program's current_rule_release_id
        conn.execute(
            queries.get("set_program_current_rule_release"),
            {"program_id": program_id, "release_id": release_id},
        )

        conn.execute(
            queries.get("insert_program_rule_review"),
            {
                "review_id": uuid4(),
                "release_id": release_id,
                "reviewer_user_id": reviewer_user_id,
                "action": "published",
                "review_notes": notes,
            },
        )
        conn.commit()
        return dict(published)

    @staticmethod
    def reject_rule_release(
        conn,
        release_id: UUID,
        reviewer_user_id: UUID | None,
        reason: str,
        *, expected_program_id: UUID | None = None,
    ) -> dict:
        release = _locked_release(conn, release_id, expected_program_id)
        if release["status"] not in ("draft", "reviewing"):
            raise HTTPException(
                409, f"cannot reject release with status '{release['status']}'"
            )

        rejected = conn.execute(
            queries.get("update_program_rule_release_status"),
            {
                "release_id": release_id,
                "status": "rejected",
                "approved_by_user_id": reviewer_user_id,
                "approved_at": None,
            },
        ).fetchone()

        conn.execute(
            queries.get("insert_program_rule_review"),
            {
                "review_id": uuid4(),
                "release_id": release_id,
                "reviewer_user_id": reviewer_user_id,
                "action": "rejected",
                "review_notes": reason,
            },
        )
        conn.commit()
        return dict(rejected)

    @staticmethod
    def diff_rule_releases(
        conn,
        program_id: UUID,
        release_id_a: UUID,
        release_id_b: UUID,
    ) -> dict:
        rel_a = conn.execute(
            queries.get("get_program_rule_release"), {"release_id": release_id_a}
        ).fetchone()
        rel_b = conn.execute(
            queries.get("get_program_rule_release"), {"release_id": release_id_b}
        ).fetchone()
        if not rel_a:
            raise HTTPException(404, f"rule release {release_id_a} not found")
        if not rel_b:
            raise HTTPException(404, f"rule release {release_id_b} not found")
        if rel_a["program_id"] != program_id or rel_b["program_id"] != program_id:
            raise HTTPException(404, "release does not belong to the requested program")

        tree_a = rel_a["rules_tree"]
        tree_b = rel_b["rules_tree"]

        def _get_nodes(node: dict, out: dict[str, dict]) -> None:
            node_id = node.get("nodeId") or node.get("node_id")
            if node_id:
                out[node_id] = node
            for child in node.get("children", []):
                _get_nodes(child, out)

        nodes_a: dict[str, dict] = {}
        nodes_b: dict[str, dict] = {}
        _get_nodes(tree_a, nodes_a)
        _get_nodes(tree_b, nodes_b)

        added = [node_id for node_id in nodes_b if node_id not in nodes_a]
        removed = [node_id for node_id in nodes_a if node_id not in nodes_b]
        modified = []

        for node_id, na in nodes_a.items():
            if node_id in nodes_b:
                nb = nodes_b[node_id]
                diffs = {}
                for key in ("nodeType", "coverageDisposition", "courseCodes", "count", "minCredits", "minGrade", "unresolvedReason"):
                    if na.get(key) != nb.get(key):
                        diffs[key] = {"old": na.get(key), "new": nb.get(key)}
                if diffs:
                    modified.append({"nodeId": node_id, "changes": diffs})

        return {
            "programId": str(program_id),
            "releaseIdA": str(release_id_a),
            "versionA": rel_a["release_version"],
            "releaseIdB": str(release_id_b),
            "versionB": rel_b["release_version"],
            "addedNodeIds": added,
            "removedNodeIds": removed,
            "modifiedNodes": modified,
            "digestMatch": rel_a["rules_digest"] == rel_b["rules_digest"],
        }
