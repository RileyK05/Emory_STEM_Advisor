"""Persisted program comparisons. Overlays never become academic-record attempts."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.advising.audit_engine import DeterministicAuditEngine
from app.advising.audit_service import DegreeAuditService
from app.advising.comparison import compare_mentions
from app.advising.research_repository import ProgramItem, ProgramPin, ProgramRead, SupportedText
from app.auth.capabilities import check_workspace_access
from app.auth.models import Principal
from app.config import load_settings
from app.db import queries
from app.records.service import AcademicRecordService
from app.util.digest import json_digest


def _overlay_attempts(attempts: list[dict], add_codes: list[str], remove_codes: list[str]) -> list[dict]:
    removed = {code.strip().upper() for code in remove_codes if code.strip()}
    copied = [
        dict(attempt)
        for attempt in attempts
        if str(attempt.get("submitted_course_code") or "").upper() not in removed
    ]
    for code in sorted({code.strip().upper() for code in add_codes if code.strip()}):
        copied.append({
            "attempt_id": f"overlay-{code}",
            "submitted_course_code": code,
            "status": "planned",
            "grade": None,
            "attempted_credits": 0.0,
            "earned_credits": 0.0,
            "is_verified": False,
            "provenance": "overlay",
        })
    return copied


def _unresolved_count(nodes) -> int:
    total = 0
    for node in nodes:
        if node.status == "unresolved" or node.unresolved_reason:
            total += 1
        total += _unresolved_count(node.children)
    return total


def _program_read(conn, program: dict) -> ProgramRead:
    rows = conn.execute(
        queries.get("list_program_items"),
        {"program_id": program["program_id"]},
    ).fetchall()
    items = []
    for row in rows:
        codes = tuple(code for code in (row["courses"] or []) if code)
        items.append(ProgramItem(
            position=row["position"],
            section=row["section"],
            line=SupportedText(text=row["text"] or ""),
            mentioned_course_codes=codes,
            choose=row["choose"],
        ))
    return ProgramRead(
        pin=ProgramPin(
            program_id=program["program_id"],
            title=program["title"],
            kind=program["kind"],
            degree=program.get("degree"),
            department=program.get("department"),
            source_id=program["source_id"],
            source_index_revision=program["source_index_revision"] or 0,
            parsed_content_digest=program.get("parsed_content_digest"),
            binding_status=program.get("binding_status") or "unbound",
        ),
        fields={},
        items=tuple(items),
        catalog=program.get("catalog"),
    )


class ProgramComparisonService:
    @classmethod
    def create(
        cls,
        conn,
        workspace_key: str,
        program_ids: list[UUID],
        add_course_codes: list[str],
        remove_course_codes: list[str],
        actor: Principal,
    ) -> dict[str, Any]:
        if not check_workspace_access(conn, actor, workspace_key, "all_records", action="write"):
            raise HTTPException(403, "access to academic record is denied")
        distinct = list(dict.fromkeys(program_ids))
        if len(distinct) != len(program_ids) or not 2 <= len(program_ids) <= 3:
            raise HTTPException(422, "compare two or three distinct programs")

        record_data = AcademicRecordService.get_record(
            conn, workspace_key, actor, create=True
        )
        record = record_data["record"]
        attempts = list(record_data["attempts"])
        overlay = {
            "addCourseCodes": sorted({code.strip().upper() for code in add_course_codes if code.strip()}),
            "removeCourseCodes": sorted({code.strip().upper() for code in remove_course_codes if code.strip()}),
        }
        overlay_digest = json_digest(overlay)
        program_set_digest = json_digest([str(program_id) for program_id in sorted(program_ids, key=str)])
        existing = conn.execute(
            queries.get("find_program_comparison"),
            {
                "workspace_key": workspace_key,
                "record_id": record["record_id"],
                "record_revision": record["record_revision"],
                "program_set_digest": program_set_digest,
                "overlay_digest": overlay_digest,
            },
        ).fetchone()
        if existing:
            return cls.get(conn, workspace_key, existing["comparison_id"], actor)

        programs = []
        for program_id in program_ids:
            program = conn.execute(
                queries.get("get_program_inspection_detail"),
                {"program_id": program_id},
            ).fetchone()
            if not program:
                raise HTTPException(404, f"program {program_id} not found")
            programs.append(dict(program))

        reads = [_program_read(conn, program) for program in programs]
        mentions = compare_mentions(reads)
        shared = sorted(mentions.shared)
        unresolved_counts = []
        members = []
        for position, program in enumerate(programs, start=1):
            saved = DegreeAuditService.run_audit(conn, workspace_key, program["program_id"], actor)
            release = conn.execute(
                queries.get("get_program_rule_release"),
                {"release_id": program["current_rule_release_id"]},
            ).fetchone()
            overlay_result = DeterministicAuditEngine(
                rules_tree=release["rules_tree"],
                attempts=_overlay_attempts(attempts, overlay["addCourseCodes"], overlay["removeCourseCodes"]),
                rules_digest=release["rules_digest"],
                max_search_states=load_settings().audit_max_search_states,
            ).run()
            unresolved = _unresolved_count(overlay_result.rule_evaluations)
            unresolved_counts.append(unresolved)
            members.append({
                "position": position,
                "program_id": program["program_id"],
                "rule_release_id": saved["audit"]["rule_release_id"],
                "audit_id": saved["audit"]["audit_id"],
                "overlay_status": overlay_result.overall_status,
                "unresolved_count": unresolved,
            })

        comparable = len(set(unresolved_counts)) == 1
        reason = None if comparable else "unequal unresolved coverage"
        comparison_id = uuid4()
        conn.execute(
            queries.get("insert_program_comparison"),
            {
                "comparison_id": comparison_id,
                "workspace_key": workspace_key,
                "record_id": record["record_id"],
                "record_revision": record["record_revision"],
                "program_set_digest": program_set_digest,
                "overlay": Jsonb(overlay),
                "overlay_digest": overlay_digest,
                "sharing_policy": "unknown",
                "mention_kind": mentions.kind,
                "shared_mentions": Jsonb(shared),
                "credit_savings": None,
                "coverage_comparable": comparable,
                "ranking_blocked_reason": reason,
            },
        )
        for member in members:
            conn.execute(
                queries.get("insert_program_comparison_member"),
                {"comparison_id": comparison_id, **member},
            )
        return cls.get(conn, workspace_key, comparison_id, actor)

    @classmethod
    def get(cls, conn, workspace_key: str, comparison_id: UUID, actor: Principal) -> dict[str, Any]:
        if not check_workspace_access(conn, actor, workspace_key, "all_records", action="read"):
            raise HTTPException(403, "access to academic record is denied")
        rows = conn.execute(
            queries.get("get_program_comparison"),
            {"comparison_id": comparison_id},
        ).fetchall()
        if not rows:
            raise HTTPException(404, f"comparison {comparison_id} not found")
        if rows[0]["workspace_key"] != workspace_key:
            raise HTTPException(403, "comparison belongs to a different workspace")
        first = rows[0]
        return {
            "comparison_id": first["comparison_id"],
            "workspace_key": first["workspace_key"],
            "record_id": first["record_id"],
            "record_revision": first["record_revision"],
            "sharing_policy": first["sharing_policy"],
            "mention_kind": first["mention_kind"],
            "shared_mentions": list(first["shared_mentions"] or []),
            "credit_savings": first["credit_savings"],
            "coverage_comparable": first["coverage_comparable"],
            "ranking_blocked_reason": first["ranking_blocked_reason"],
            "created_at": first["created_at"],
            "members": [dict(row) for row in rows],
        }

    @classmethod
    def list_for_workspace(cls, conn, workspace_key: str, actor: Principal) -> list[dict[str, Any]]:
        if not check_workspace_access(conn, actor, workspace_key, "all_records", action="read"):
            raise HTTPException(403, "access to academic record is denied")
        rows = conn.execute(
            queries.get("list_program_comparisons"),
            {"workspace_key": workspace_key},
        ).fetchall()
        return [dict(row) for row in rows]
