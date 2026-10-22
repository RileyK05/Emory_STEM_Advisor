import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.db import queries
from app.ingest.provenance import ProvenanceError, validate_curator_proposal
from app.util.json import loose_jsonb

logger = logging.getLogger(__name__)


class SourceReviewService:
    @staticmethod
    def get_source_detail(conn, source_id: UUID) -> dict:
        row = conn.execute(
            queries.get("get_source_detail_for_review"), {"source_id": source_id}
        ).fetchone()
        if not row:
            raise HTTPException(404, f"source {source_id} not found")

        proposals = conn.execute(
            queries.get("list_source_review_proposals_for_source"), {"source_id": source_id}
        ).fetchall()
        events = conn.execute(
            queries.get("list_source_lifecycle_events"), {"source_id": source_id}
        ).fetchall()
        dep_counts = conn.execute(
            queries.get("get_source_dependency_counts"), {"source_id": source_id}
        ).fetchone()
        dep_programs = conn.execute(
            queries.get("list_source_dependent_programs"), {"source_id": source_id}
        ).fetchall()

        pending_proposal = next(
            (dict(p) for p in proposals if p["status"] == "pending"), None
        )

        return {
            "source": dict(row),
            "pendingProposal": pending_proposal,
            "proposals": [dict(p) for p in proposals],
            "lifecycleEvents": [dict(e) for e in events],
            "dependencies": {
                "programCount": dep_counts["program_count"] if dep_counts else 0,
                "courseCount": dep_counts["course_count"] if dep_counts else 0,
                "gapCount": dep_counts["gap_count"] if dep_counts else 0,
                "researchRunCount": dep_counts["research_run_count"] if dep_counts else 0,
                "programs": [dict(dp) for dp in dep_programs],
            },
        }

    @staticmethod
    def get_impact_preview(conn, source_id: UUID) -> dict:
        row = conn.execute(
            queries.get("get_source_detail_for_review"), {"source_id": source_id}
        ).fetchone()
        if not row:
            raise HTTPException(404, f"source {source_id} not found")

        dep_counts = conn.execute(
            queries.get("get_source_dependency_counts"), {"source_id": source_id}
        ).fetchone()
        dep_programs = conn.execute(
            queries.get("list_source_dependent_programs"), {"source_id": source_id}
        ).fetchall()

        return {
            "sourceId": str(source_id),
            "filename": row["filename"],
            "programCount": dep_counts["program_count"] if dep_counts else 0,
            "courseCount": dep_counts["course_count"] if dep_counts else 0,
            "gapCount": dep_counts["gap_count"] if dep_counts else 0,
            "researchRunCount": dep_counts["research_run_count"] if dep_counts else 0,
            "programs": [dict(dp) for dp in dep_programs],
        }

    @staticmethod
    def create_proposal(
        conn,
        source_id: UUID,
        actor_user_id: UUID | None,
        expected_metadata_revision: int,
        raw_proposal: dict,
    ) -> dict:
        row = conn.execute(
            queries.get("get_source_detail_for_review"), {"source_id": source_id}
        ).fetchone()
        if not row:
            raise HTTPException(404, f"source {source_id} not found")

        current_revision = row["metadata_revision"] or 1
        if expected_metadata_revision != current_revision:
            raise HTTPException(
                409,
                f"metadata revision mismatch: expected {expected_metadata_revision}, currently {current_revision}",
            )

        try:
            validated = validate_curator_proposal(raw_proposal)
        except ProvenanceError as exc:
            raise HTTPException(422, str(exc)) from exc

        proposal_id = uuid4()
        params = {
            "proposal_id": proposal_id,
            "source_id": source_id,
            "expected_metadata_revision": expected_metadata_revision,
            "proposed_by_user_id": actor_user_id,
            "status": "pending",
            "source_category": validated["source_category"],
            "classification_origin": validated["classification_origin"],
            "publisher": validated["publisher"],
            "source_url": validated["source_url"],
            "edition": validated["edition"],
            "geography": validated["geography"],
            "occupation_id": validated["occupation_id"],
            "published_at": validated["published_at"],
            "acquired_at": validated["acquired_at"],
            "attribution": validated["attribution"],
            "title": validated["title"],
            "institution": validated["institution"],
            "issuing_unit": validated["issuing_unit"],
            "document_roles": Jsonb(sorted(set(validated["document_roles"]))),
            "applicability": Jsonb(
                {k: sorted(set(v)) for k, v in validated["applicability"].items()}
            ),
            "applicability_status": validated["applicability_status"],
            "lifecycle_status": validated["lifecycle_status"],
            "replacement_intent": validated["replacement_intent"],
            "review_notes": validated["review_notes"],
        }

        created = conn.execute(queries.get("insert_source_review_proposal"), params).fetchone()

        # Record lifecycle event
        conn.execute(
            queries.get("insert_source_lifecycle_event"),
            {
                "event_id": uuid4(),
                "source_id": source_id,
                "event_type": "proposal_created",
                "proposal_id": proposal_id,
                "metadata_revision": current_revision,
                "actor_user_id": actor_user_id,
                "details": loose_jsonb({
                    "proposal_id": str(proposal_id),
                    "category": validated["source_category"],
                    "title": validated["title"],
                }),
            },
        )
        conn.commit()
        return dict(created)

    @staticmethod
    def approve_proposal(
        conn,
        source_id: UUID,
        proposal_id: UUID,
        reviewer_user_id: UUID | None,
    ) -> dict:
        proposal = conn.execute(
            queries.get("get_source_review_proposal"), {"proposal_id": proposal_id}
        ).fetchone()
        if not proposal:
            raise HTTPException(404, f"proposal {proposal_id} not found")
        if proposal["source_id"] != source_id:
            raise HTTPException(400, "proposal does not belong to specified source")
        if proposal["status"] != "pending":
            raise HTTPException(409, f"proposal status is '{proposal['status']}', expected 'pending'")

        source = conn.execute(
            queries.get("get_source_detail_for_review"), {"source_id": source_id}
        ).fetchone()
        if not source:
            raise HTTPException(404, f"source {source_id} not found")

        current_revision = source["metadata_revision"] or 1
        if proposal["expected_metadata_revision"] != current_revision:
            raise HTTPException(
                409,
                f"metadata revision conflict: proposal expected {proposal['expected_metadata_revision']}, source currently at {current_revision}",
            )

        now = datetime.now(UTC)
        # Atomically update provenance
        updated_provenance = conn.execute(
            queries.get("update_source_provenance_from_review"),
            {
                "source_id": source_id,
                "source_category": proposal["source_category"],
                "classification_origin": "verified_curator",
                "publisher": proposal["publisher"],
                "source_url": proposal["source_url"],
                "edition": proposal["edition"],
                "geography": proposal["geography"],
                "occupation_id": proposal["occupation_id"],
                "published_at": proposal["published_at"],
                "acquired_at": proposal["acquired_at"],
                "attribution": proposal["attribution"],
                "title": proposal["title"],
                "institution": proposal["institution"],
                "issuing_unit": proposal["issuing_unit"],
                "document_roles": Jsonb(proposal["document_roles"]),
                "applicability": Jsonb(proposal["applicability"]),
                "applicability_status": proposal["applicability_status"],
                "lifecycle_status": proposal["lifecycle_status"],
                "expected_metadata_revision": current_revision,
            },
        ).fetchone()

        if not updated_provenance:
            raise HTTPException(
                409,
                "concurrent modification detected while applying proposal to source provenance",
            )

        new_revision = updated_provenance["metadata_revision"]

        # If lifecycle status changed to deprecated or archived, update sources.is_current
        if proposal["lifecycle_status"] in ("deprecated", "archived"):
            conn.execute(
                queries.get("update_source_is_current"),
                {"source_id": source_id, "is_current": False},
            )
        elif proposal["lifecycle_status"] == "active":
            conn.execute(
                queries.get("update_source_is_current"),
                {"source_id": source_id, "is_current": True},
            )

        # Mark proposal approved
        approved_proposal = conn.execute(
            queries.get("update_source_review_proposal_status"),
            {
                "proposal_id": proposal_id,
                "status": "approved",
                "reviewed_by_user_id": reviewer_user_id,
                "reviewed_at": now,
                "rejection_reason": None,
            },
        ).fetchone()

        # Supersede any other pending proposals for this source
        all_proposals = conn.execute(
            queries.get("list_source_review_proposals_for_source"), {"source_id": source_id}
        ).fetchall()
        for p in all_proposals:
            if p["proposal_id"] != proposal_id and p["status"] == "pending":
                conn.execute(
                    queries.get("update_source_review_proposal_status"),
                    {
                        "proposal_id": p["proposal_id"],
                        "status": "superseded",
                        "reviewed_by_user_id": reviewer_user_id,
                        "reviewed_at": now,
                        "rejection_reason": "Superseded by approval of proposal " + str(proposal_id),
                    },
                )

        # Record lifecycle event
        conn.execute(
            queries.get("insert_source_lifecycle_event"),
            {
                "event_id": uuid4(),
                "source_id": source_id,
                "event_type": "proposal_approved",
                "proposal_id": proposal_id,
                "metadata_revision": new_revision,
                "actor_user_id": reviewer_user_id,
                "details": loose_jsonb({
                    "proposal_id": str(proposal_id),
                    "old_revision": current_revision,
                    "new_revision": new_revision,
                    "lifecycle_status": proposal["lifecycle_status"],
                }),
            },
        )

        # Transactional outbox event for async invalidation
        conn.execute(
            queries.get("insert_source_invalidation_outbox"),
            {
                "outbox_id": uuid4(),
                "source_id": source_id,
                "event_type": "source_provenance_updated",
                "payload": loose_jsonb({
                    "source_id": str(source_id),
                    "old_revision": current_revision,
                    "new_revision": new_revision,
                    "category": proposal["source_category"],
                    "lifecycle_status": proposal["lifecycle_status"],
                    "approved_at": now.isoformat(),
                }),
            },
        )

        conn.commit()
        return dict(approved_proposal)

    @staticmethod
    def reject_proposal(
        conn,
        source_id: UUID,
        proposal_id: UUID,
        reviewer_user_id: UUID | None,
        reason: str,
    ) -> dict:
        proposal = conn.execute(
            queries.get("get_source_review_proposal"), {"proposal_id": proposal_id}
        ).fetchone()
        if not proposal:
            raise HTTPException(404, f"proposal {proposal_id} not found")
        if proposal["source_id"] != source_id:
            raise HTTPException(400, "proposal does not belong to specified source")
        if proposal["status"] != "pending":
            raise HTTPException(409, f"proposal status is '{proposal['status']}', expected 'pending'")

        now = datetime.now(UTC)
        rejected = conn.execute(
            queries.get("update_source_review_proposal_status"),
            {
                "proposal_id": proposal_id,
                "status": "rejected",
                "reviewed_by_user_id": reviewer_user_id,
                "reviewed_at": now,
                "rejection_reason": reason,
            },
        ).fetchone()

        source = conn.execute(
            queries.get("get_source_detail_for_review"), {"source_id": source_id}
        ).fetchone()
        current_revision = source["metadata_revision"] if source else 1

        conn.execute(
            queries.get("insert_source_lifecycle_event"),
            {
                "event_id": uuid4(),
                "source_id": source_id,
                "event_type": "proposal_rejected",
                "proposal_id": proposal_id,
                "metadata_revision": current_revision,
                "actor_user_id": reviewer_user_id,
                "details": loose_jsonb({
                    "proposal_id": str(proposal_id),
                    "rejection_reason": reason,
                }),
            },
        )
        conn.commit()
        return dict(rejected)

    @staticmethod
    def change_lifecycle_status(
        conn,
        source_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        expected_metadata_revision: int,
        notes: str = "",
    ) -> dict:
        action_map = {
            "activate": ("active", True, "activated"),
            "deprecate": ("deprecated", False, "deprecated"),
            "archive": ("archived", False, "archived"),
        }
        if action not in action_map:
            raise HTTPException(
                422, f"action must be one of {sorted(action_map.keys())}"
            )

        target_status, is_current, event_type = action_map[action]

        source = conn.execute(
            queries.get("get_source_detail_for_review"), {"source_id": source_id}
        ).fetchone()
        if not source:
            raise HTTPException(404, f"source {source_id} not found")

        current_revision = source["metadata_revision"] or 1
        if expected_metadata_revision != current_revision:
            raise HTTPException(
                409,
                f"metadata revision conflict: expected {expected_metadata_revision}, currently {current_revision}",
            )

        updated_provenance = conn.execute(
            queries.get("update_source_lifecycle_status"),
            {
                "source_id": source_id,
                "lifecycle_status": target_status,
                "expected_metadata_revision": current_revision,
            },
        ).fetchone()

        if not updated_provenance:
            raise HTTPException(
                409, "concurrent modification detected while changing lifecycle status"
            )

        conn.execute(
            queries.get("update_source_is_current"),
            {"source_id": source_id, "is_current": is_current},
        )

        new_revision = updated_provenance["metadata_revision"]

        conn.execute(
            queries.get("insert_source_lifecycle_event"),
            {
                "event_id": uuid4(),
                "source_id": source_id,
                "event_type": event_type,
                "proposal_id": None,
                "metadata_revision": new_revision,
                "actor_user_id": actor_user_id,
                "details": loose_jsonb({
                    "action": action,
                    "target_status": target_status,
                    "notes": notes,
                }),
            },
        )

        # Invalidation outbox event
        conn.execute(
            queries.get("insert_source_invalidation_outbox"),
            {
                "outbox_id": uuid4(),
                "source_id": source_id,
                "event_type": f"source_{target_status}",
                "payload": loose_jsonb({
                    "source_id": str(source_id),
                    "old_revision": current_revision,
                    "new_revision": new_revision,
                    "lifecycle_status": target_status,
                    "is_current": is_current,
                }),
            },
        )

        conn.commit()
        return dict(updated_provenance)
