"""Service layer for deterministic degree audits, evaluations, and staleness detection."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.advising.audit_engine import DeterministicAuditEngine
from app.auth.capabilities import check_workspace_access
from app.auth.models import Principal
from app.config import load_settings
from app.db import queries
from app.programs.rules import RuleValidationError
from app.records.service import AcademicRecordService

logger = logging.getLogger(__name__)


def _clean_json(data: Any) -> Jsonb:
    return Jsonb(data, dumps=lambda o: json.dumps(o, default=str))


class DegreeAuditService:
    @classmethod
    def run_audit(
        cls,
        conn,
        workspace_key: str,
        program_id: UUID,
        actor: Principal,
    ) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="write"
        ):
            raise HTTPException(403, "access to academic record is denied")

        # 1. Fetch academic record and attempts. The audit creates derived rows
        # in the student's workspace, so a read-only support grant cannot run it.
        record_data = AcademicRecordService.get_record(
            conn, workspace_key, actor, create=True
        )
        record = record_data["record"]
        attempts = record_data["attempts"]

        # 2. Fetch program and published rule release
        program = conn.execute(
            queries.get("get_program_inspection_detail"),
            {"program_id": program_id},
        ).fetchone()
        if not program:
            raise HTTPException(404, f"program {program_id} not found")

        if (not program["is_active"] or not program["source_is_current"]
                or program["source_status"] != "indexed"
                or program["source_lifecycle_status"] in {"staged", "deprecated", "archived"}
                or program["source_index_revision"] != program["current_source_index_revision"]
                or program["source_sha256"] != program["current_source_sha256"]):
            raise HTTPException(409, "program evidence is retired or superseded; review current rules")
        current_release_id = program["current_rule_release_id"]
        if not current_release_id:
            raise HTTPException(
                400,
                f"program '{program['title']}' has no active published rule release",
            )

        release = conn.execute(
            queries.get("get_program_rule_release"),
            {"release_id": current_release_id},
        ).fetchone()
        if not release:
            raise HTTPException(404, f"rule release {current_release_id} not found")
        if (release["status"] != "published"
                or release["program_id"] != program_id
                or release["source_id"] != program["source_id"]
                or release["source_index_revision"] != program["source_index_revision"]
                or release["source_sha256"] != program["source_sha256"]
                or release["bound_program_digest"] != program["parsed_content_digest"]
                or release["source_metadata_revision"] != (program["source_metadata_revision"] or 0)):
            raise HTTPException(409, "program rule release does not match its current evidence")

        # 3. Deterministic evaluation
        try:
            engine = DeterministicAuditEngine(
                rules_tree=release["rules_tree"], attempts=attempts,
                rules_digest=release["rules_digest"],
                max_search_states=load_settings().audit_max_search_states,
            )
            result = engine.run()
        except (RuleValidationError, ValueError) as exc:
            raise HTTPException(422, f"unsupported audit inputs: {exc}") from exc

        # 4. Save degree audit
        audit_id = uuid4()
        saved_audit = conn.execute(
            queries.get("insert_degree_audit"),
            {
                "audit_id": audit_id,
                "workspace_key": workspace_key,
                "program_id": program_id,
                "rule_release_id": current_release_id,
                "record_id": record["record_id"],
                "record_revision": record["record_revision"],
                "rules_digest": release["rules_digest"],
                "overall_status": result.overall_status,
                "is_stale": False,
                "completed_credits": result.completed_credits,
                "in_progress_credits": result.in_progress_credits,
                "unverified_credits": result.unverified_credits,
                "results_summary": _clean_json(result.results_summary),
                "blockers": _clean_json(result.blockers),
                "assumptions": _clean_json(result.assumptions),
            },
        ).fetchone()

        # 5. Save rule evaluations
        saved_evals = []
        for ev in result.rule_evaluations:
            ev_id = uuid4()
            row = conn.execute(
                queries.get("insert_audit_rule_evaluation"),
                {
                    "evaluation_id": ev_id,
                    "audit_id": audit_id,
                    "node_id": ev.node_id,
                    "parent_node_id": ev.parent_node_id,
                    "node_type": ev.node_type,
                    "title": ev.title,
                    "status": ev.status,
                    "allocated_attempt_ids": _clean_json(ev.allocated_attempt_ids),
                    "allocated_credits": ev.allocated_credits,
                    "required_count": ev.required_count,
                    "satisfied_count": ev.satisfied_count,
                    "unresolved_reason": ev.unresolved_reason,
                    "raw_position": ev.raw_position,
                },
            ).fetchone()
            saved_evals.append(dict(row))

        return {
            "audit": dict(saved_audit),
            "evaluations": saved_evals,
            "programTitle": program["title"],
            "programKind": program["kind"],
        }

    @classmethod
    def get_audit(
        cls,
        conn,
        workspace_key: str,
        audit_id: UUID,
        actor: Principal,
    ) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="read"
        ):
            raise HTTPException(403, "access to academic record is denied")

        row = conn.execute(
            queries.get("get_degree_audit_by_id"),
            {"audit_id": audit_id},
        ).fetchone()
        if not row:
            raise HTTPException(404, f"audit {audit_id} not found")

        audit_data = dict(row)
        if audit_data["workspace_key"] != workspace_key:
            raise HTTPException(403, "audit belongs to a different workspace")

        # List and detail derive the same current validity in the named-query view.
        evals = conn.execute(
            queries.get("list_audit_rule_evaluations"),
            {"audit_id": audit_id},
        ).fetchall()

        return {
            "audit": audit_data,
            "evaluations": [dict(e) for e in evals],
            "programTitle": audit_data["program_title"],
            "programKind": audit_data["program_kind"],
        }

    @classmethod
    def list_audits(
        cls,
        conn,
        workspace_key: str,
        actor: Principal,
    ) -> list[dict[str, Any]]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="read"
        ):
            raise HTTPException(403, "access to academic record is denied")

        rows = conn.execute(
            queries.get("list_degree_audits_for_workspace"),
            {"workspace_key": workspace_key},
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def list_auditable_programs(cls, conn) -> list[dict[str, Any]]:
        """Published programs whose current release still matches source evidence."""
        rows = conn.execute(queries.get("list_auditable_programs")).fetchall()
        return [dict(row) for row in rows]
