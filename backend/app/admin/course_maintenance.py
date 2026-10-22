"""Reviewed, versioned admin maintenance of course identities.

Every write here is deterministic: the requisites parser is the only thing that
produces edges, an admin edit never acquires Atlas authority, and a stale
revision is rejected instead of merged. No model participates.
"""

from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.atlas.course_writer import course_uuid, replace_course_requisites
from app.atlas.records import normalize_code
from app.atlas.requisites import PARSER_VERSION, parse_requisites
from app.db import queries

_CHANGE_KINDS = {"create", "update", "retire", "restore"}
_ADMIN_CONTROLLED = ("admin_correction", "admin_created")


def _limit(value: int, maximum: int = 200) -> int:
    if value < 1:
        raise HTTPException(422, "limit must be positive")
    return min(value, maximum)


def _required_text(value: str | None, field: str, maximum: int = 2000) -> str:
    text = (value or "").strip()
    if not text:
        raise HTTPException(422, f"{field} is required")
    if len(text) > maximum:
        raise HTTPException(422, f"{field} exceeds {maximum} characters")
    return text


def _normalized_code(value: str | None) -> str:
    code = normalize_code((value or "").strip())
    if code is None:
        raise HTTPException(422, "course code is not a valid subject and number")
    return code


def _base_value(state: dict) -> dict:
    return {
        "title": state["title"],
        "requisitesRaw": state["requisites_raw"],
        "lifecycle": state["lifecycle"],
        "authority": state["authority"],
        "adminRevision": state["admin_revision"],
        "sourceId": str(state["source_id"]),
    }


def _current_codes(conn, collection_id: UUID) -> set[str]:
    return {
        row["code"]
        for row in conn.execute(
            queries.get("list_collection_course_codes"), {"collection_id": collection_id}
        ).fetchall()
    }


class CourseMaintenanceService:
    # ---- reads -----------------------------------------------------------

    @staticmethod
    def list_courses(
        conn,
        *,
        collection_id: UUID,
        query: str = "",
        subject: str = "",
        lifecycle: str = "",
        authority: str = "",
        campus: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        if lifecycle and lifecycle not in ("current", "retired"):
            raise HTTPException(422, "lifecycle must be current or retired")
        if authority and authority not in ("atlas", "catalog", *_ADMIN_CONTROLLED):
            raise HTTPException(422, "unknown authority filter")
        if campus and campus not in ("atlanta", "oxford"):
            raise HTTPException(422, "campus must be atlanta or oxford")
        params = {
            "collection_id": collection_id,
            "pattern": f"%{query.strip().lower()}%" if query.strip() else "",
            "subject": subject.strip().upper(),
            "lifecycle": lifecycle,
            "authority": authority,
            "campus": campus,
            "limit": _limit(limit),
            "offset": offset,
        }
        rows = conn.execute(queries.get("list_admin_courses"), params).fetchall()
        total = conn.execute(queries.get("count_admin_courses"), params).fetchone()["total"]
        subjects = conn.execute(
            queries.get("list_admin_course_subjects"), {"collection_id": collection_id}
        ).fetchall()
        conn.commit()
        return {
            "courses": [dict(row) for row in rows],
            "total": total,
            "subjects": [row["subject"] for row in subjects],
        }

    @staticmethod
    def get_course(conn, *, collection_id: UUID, code: str) -> dict:
        normalized = _normalized_code(code)
        row = conn.execute(
            queries.get("get_admin_course"),
            {"collection_id": collection_id, "code": normalized},
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"course {normalized} was not found")
        current = _current_codes(conn, collection_id)
        mentions = [
            {
                "kind": mention["kind"],
                "targetCode": mention["target_code"],
                "resolved": bool(mention["target_course_id"]),
                "position": mention["position"],
            }
            for mention in conn.execute(
                queries.get("list_mentions_for_course"), {"course_id": row["course_id"]}
            ).fetchall()
        ]
        nodes = [
            dict(node)
            for node in conn.execute(
                queries.get("list_nodes_for_course"), {"course_id": row["course_id"]}
            ).fetchall()
        ]
        programs = [
            dict(program)
            for program in conn.execute(
                queries.get("programs_requiring_course"),
                {"collection_id": collection_id, "code": normalized},
            ).fetchall()
        ]
        dependents = [
            dict(dependent)
            for dependent in conn.execute(
                queries.get("list_admin_course_dependents"),
                {"collection_id": collection_id, "code": normalized},
            ).fetchall()
        ]
        changes = [
            dict(change)
            for change in conn.execute(
                queries.get("list_course_changes_for_course"),
                {"collection_id": collection_id, "code": normalized},
            ).fetchall()
        ]
        conn.commit()
        return {
            "course": dict(row),
            "mentions": mentions,
            "nodes": nodes,
            "programs": programs,
            "dependents": dependents,
            "changes": changes,
            "currentCodes": sorted(current),
        }

    @staticmethod
    def list_conflicts(conn, *, collection_id: UUID, limit: int = 50) -> list[dict]:
        rows = conn.execute(
            queries.get("list_conflicted_course_changes"),
            {"collection_id": collection_id, "limit": _limit(limit)},
        ).fetchall()
        conn.commit()
        return [dict(row) for row in rows]

    # ---- change creation -------------------------------------------------

    @staticmethod
    def create_change(
        conn,
        *,
        collection_id: UUID,
        payload: dict,
        actor_user_id: UUID | None,
    ) -> dict:
        code = _normalized_code(payload.get("code"))
        reason = _required_text(payload.get("reason"), "reason")
        expected_revision = payload.get("expectedRevision")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise HTTPException(422, "expectedRevision must be a non-negative integer")

        state = conn.execute(
            queries.get("get_course_admin_state"),
            {"collection_id": collection_id, "code": code},
        ).fetchone()

        kind = payload.get("changeKind") or ("update" if state is not None else "create")
        if kind not in _CHANGE_KINDS:
            raise HTTPException(422, "changeKind must be create, update, retire or restore")

        if conn.execute(
            queries.get("get_open_draft_for_course"),
            {"collection_id": collection_id, "code": code},
        ).fetchone() is not None:
            raise HTTPException(409, "an open draft already exists for this course")

        proposed: dict = {}
        if kind == "create":
            if state is not None:
                raise HTTPException(
                    409,
                    "this course identity already exists; restore or edit it instead",
                )
            if expected_revision != 0:
                raise HTTPException(409, "a new course starts at revision 0")
            proposed = {
                "title": _required_text(payload.get("title"), "title", 400),
                "requisitesRaw": (payload.get("requisitesRaw") or "").strip(),
                "sourceId": payload.get("sourceId"),
            }
        elif kind == "update":
            if state is None:
                raise HTTPException(404, f"course {code} was not found")
            if expected_revision != state["admin_revision"]:
                raise HTTPException(
                    409,
                    f"stale revision: expected {expected_revision}, "
                    f"currently {state['admin_revision']}",
                )
            proposed = {
                "title": _required_text(payload.get("title"), "title", 400),
                "requisitesRaw": (payload.get("requisitesRaw") or "").strip(),
                "sourceId": payload.get("sourceId"),
            }
        else:
            if state is None:
                raise HTTPException(404, f"course {code} was not found")
            if expected_revision != state["admin_revision"]:
                raise HTTPException(
                    409,
                    f"stale revision: expected {expected_revision}, "
                    f"currently {state['admin_revision']}",
                )
            if kind == "retire" and state["lifecycle"] != "current":
                raise HTTPException(409, "course is already retired")
            if kind == "restore" and state["lifecycle"] != "retired":
                raise HTTPException(409, "course is not retired")

        change_id = uuid4()
        created = None
        try:
            created = conn.execute(
                queries.get("insert_course_change"),
                {
                    "change_id": change_id,
                    "collection_id": collection_id,
                    "course_id": state["course_id"] if state is not None
                    else course_uuid(collection_id, code),
                    "code": code,
                    "change_kind": kind,
                    "status": "draft",
                    "expected_revision": expected_revision,
                    "base_authority": state["authority"] if state is not None else None,
                    "base_value": Jsonb(_base_value(state) if state is not None else {}),
                    "proposed_value": Jsonb(proposed),
                    "reason": reason,
                    "actor_user_id": actor_user_id,
                    "conflict_note": None,
                },
            ).fetchone()
        except UniqueViolation:
            # Two admins staged the same identity at once; the partial unique
            # index keeps exactly one open draft.
            conn.rollback()
            raise HTTPException(
                409, "an open draft already exists for this course"
            ) from None
        if created is None:
            raise HTTPException(409, "the course change could not be staged")
        conn.commit()
        return CourseMaintenanceService.get_change(conn, change_id=created["change_id"])

    @staticmethod
    def get_change(conn, *, change_id: UUID) -> dict:
        row = conn.execute(
            queries.get("get_course_change"), {"change_id": change_id}
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"course change {change_id} was not found")
        conn.commit()
        return dict(row)

    @staticmethod
    def preview_change(conn, *, change_id: UUID) -> dict:
        change = conn.execute(
            queries.get("get_course_change"), {"change_id": change_id}
        ).fetchone()
        if change is None:
            raise HTTPException(404, f"course change {change_id} was not found")
        collection_id = change["collection_id"]
        code = change["code"]
        state = conn.execute(
            queries.get("get_course_admin_state"),
            {"collection_id": collection_id, "code": code},
        ).fetchone()
        proposed = change["proposed_value"] or {}
        kind = change["change_kind"]
        current = dict(state) if state is not None else None

        changed: list[str] = []
        if kind == "create":
            changed = ["title", "requisitesRaw", "lifecycle", "authority"]
        elif kind == "update" and current is not None:
            if proposed.get("title") != current["title"]:
                changed.append("title")
            if (proposed.get("requisitesRaw") or "") != (current["requisites_raw"] or ""):
                changed.append("requisitesRaw")
        elif kind == "retire" or kind == "restore":
            changed = ["lifecycle"]

        parsed = None
        mentions: list[dict] = []
        unresolved: list[str] = []
        if kind in ("create", "update"):
            raw = proposed.get("requisitesRaw") or ""
            result = parse_requisites(raw, course_code=code)
            codes = _current_codes(conn, collection_id)
            mentions = [
                {
                    "kind": mention.kind,
                    "targetCode": mention.code,
                    "resolved": mention.code in codes,
                }
                for mention in result.mentions
            ]
            unresolved = sorted({m["targetCode"] for m in mentions if not m["resolved"]})
            parsed = {
                "status": result.status,
                "parseStatus": result.status,
                "mentions": mentions,
                "unresolved": unresolved,
                "leftover": list(result.leftover),
            }

        programs = [
            dict(program)
            for program in conn.execute(
                queries.get("programs_requiring_course"),
                {"collection_id": collection_id, "code": code},
            ).fetchall()
        ]
        dependents = [
            dict(dependent)
            for dependent in conn.execute(
                queries.get("list_admin_course_dependents"),
                {"collection_id": collection_id, "code": code},
            ).fetchall()
        ]
        stale_audits = conn.execute(
            queries.get("count_stale_audits_for_course"), {"code": code}
        ).fetchone()["total"]
        topic_builds = conn.execute(
            queries.get("count_current_topic_builds"), {"collection_id": collection_id}
        ).fetchone()["total"]

        warnings: list[str] = []
        if kind == "create" and current is not None:
            warnings.append("a course identity with this code already exists")
        if kind == "create" and state is None:
            warnings.append(
                "a new course carries admin_created authority and never acquires Atlas authority"
            )
        if parsed is not None and parsed["status"] in ("unparsed", "ambiguous"):
            warnings.append(
                "the deterministic parser did not produce prerequisite edges; "
                "the raw wording is published without invented edges"
            )
        if unresolved:
            warnings.append(
                f"{len(unresolved)} prerequisite code(s) do not resolve to a current course"
            )
        if kind == "retire" and dependents:
            warnings.append(
                f"{len(dependents)} course(s) list this code as a prerequisite; "
                "they become explicit unresolved dependencies"
            )
        conn.commit()
        return {
            "change": dict(change),
            "current": current,
            "changedFields": changed,
            "parsed": parsed,
            "programs": programs,
            "dependents": dependents,
            "staleAuditCount": stale_audits,
            "activeTopicBuilds": topic_builds,
            "warnings": warnings,
        }

    # ---- publication -----------------------------------------------------

    @staticmethod
    def publish_change(
        conn,
        *,
        change_id: UUID,
        actor_user_id: UUID | None,
    ) -> dict:
        with conn.transaction():
            change = conn.execute(
                queries.get("lock_course_change"), {"change_id": change_id}
            ).fetchone()
            if change is None:
                raise HTTPException(404, f"course change {change_id} was not found")
            if change["status"] != "draft":
                raise HTTPException(
                    409, f"change status is '{change['status']}', expected 'draft'"
                )
            collection_id = change["collection_id"]
            code = change["code"]
            kind = change["change_kind"]
            state = conn.execute(
                queries.get("get_course_admin_state"),
                {"collection_id": collection_id, "code": code},
            ).fetchone()
            proposed = conn.execute(
                queries.get("get_course_change"), {"change_id": change_id}
            ).fetchone()["proposed_value"] or {}

            published_revision = CourseMaintenanceService._apply(
                conn,
                collection_id=collection_id,
                code=code,
                kind=kind,
                expected_revision=change["expected_revision"],
                proposed=proposed,
                state=state,
            )

            conn.execute(
                queries.get("mark_degree_audits_stale_for_course"), {"code": code}
            )
            conn.execute(
                queries.get("retire_current_topic_builds"), {"collection_id": collection_id}
            )
            marked = conn.execute(
                queries.get("mark_course_change_published"),
                {
                    "change_id": change_id,
                    "published_revision": published_revision,
                    "published_by_user_id": actor_user_id,
                },
            ).fetchone()
            if marked is None:
                raise HTTPException(409, "change was published concurrently")
        return {"change": CourseMaintenanceService.get_change(conn, change_id=change_id)}

    @staticmethod
    def _apply(
        conn,
        *,
        collection_id: UUID,
        code: str,
        kind: str,
        expected_revision: int,
        proposed: dict,
        state: dict | None,
    ) -> int:
        if kind == "create":
            if state is not None:
                raise HTTPException(
                    409, "a course identity with this code now exists; restore or edit it"
                )
            source_id = proposed.get("sourceId")
            if source_id is None:
                default = conn.execute(
                    queries.get("get_default_admin_course_source"),
                    {"collection_id": collection_id},
                ).fetchone()
                source_id = default["source_id"] if default is not None else None
            if source_id is None:
                raise HTTPException(
                    422, "no indexed institutional source is available to anchor this course"
                )
            source = conn.execute(
                queries.get("get_admin_course_source"),
                {"collection_id": collection_id, "source_id": source_id},
            ).fetchone()
            if source is None:
                raise HTTPException(422, "sourceId is not an indexed current source")
            course_id = course_uuid(collection_id, code)
            locator_id = conn.execute(
                queries.get("insert_admin_course_locator"),
                {"locator_id": uuid4(), "source_id": source_id, "label": code},
            ).fetchone()["locator_id"]
            raw = proposed.get("requisitesRaw") or ""
            parsed = parse_requisites(raw, course_code=code)
            inserted = conn.execute(
                queries.get("insert_admin_course"),
                {
                    "course_id": course_id,
                    "collection_id": collection_id,
                    "source_id": source_id,
                    "locator_id": locator_id,
                    "code": code,
                    "subject": code.split(" ", 1)[0],
                    "title": proposed.get("title"),
                    "requisites_raw": raw,
                    "parse_status": parsed.status,
                    "parser_version": PARSER_VERSION,
                    "canonical_code": code,
                    "lifecycle": "current",
                    "authority": "admin_created",
                },
            ).fetchone()
            if inserted is None:
                raise HTTPException(409, "a course identity with this code now exists")
            replace_course_requisites(conn, course_id, parsed)
            conn.execute(
                queries.get("resolve_mention_targets"),
                {"course_id": course_id, "collection_id": collection_id},
            )
            conn.execute(
                queries.get("resolve_mentions_for_restored_course"),
                {"course_id": course_id, "collection_id": collection_id},
            )
            return inserted["admin_revision"]

        if state is None:
            raise HTTPException(404, f"course {code} was not found")
        if state["admin_revision"] != expected_revision:
            raise HTTPException(
                409,
                f"stale revision: expected {expected_revision}, "
                f"currently {state['admin_revision']}",
            )
        authority = (
            "admin_created" if state["authority"] == "admin_created" else "admin_correction"
        )

        if kind == "update":
            raw = proposed.get("requisitesRaw") or ""
            parsed = parse_requisites(raw, course_code=code)
            row = conn.execute(
                queries.get("update_course_from_change"),
                {
                    "course_id": state["course_id"],
                    "expected_revision": expected_revision,
                    "title": proposed.get("title"),
                    "requisites_raw": raw,
                    "parse_status": parsed.status,
                    "parser_version": PARSER_VERSION,
                    "authority": authority,
                },
            ).fetchone()
            if row is None:
                raise HTTPException(409, "course changed during publication; reload and retry")
            replace_course_requisites(conn, state["course_id"], parsed)
            conn.execute(
                queries.get("resolve_mention_targets"),
                {"course_id": state["course_id"], "collection_id": collection_id},
            )
            return row["admin_revision"]

        if kind == "retire":
            row = conn.execute(
                queries.get("update_course_lifecycle_from_change"),
                {
                    "course_id": state["course_id"],
                    "expected_revision": expected_revision,
                    "lifecycle": "retired",
                    "authority": authority,
                },
            ).fetchone()
            if row is None:
                raise HTTPException(409, "course changed during publication; reload and retry")
            conn.execute(
                queries.get("unresolve_mentions_for_retired_course"),
                {"course_id": state["course_id"]},
            )
            return row["admin_revision"]

        if kind == "restore":
            row = conn.execute(
                queries.get("update_course_lifecycle_from_change"),
                {
                    "course_id": state["course_id"],
                    "expected_revision": expected_revision,
                    "lifecycle": "current",
                    "authority": authority,
                },
            ).fetchone()
            if row is None:
                raise HTTPException(409, "course changed during publication; reload and retry")
            conn.execute(
                queries.get("resolve_mentions_for_restored_course"),
                {"course_id": state["course_id"], "collection_id": collection_id},
            )
            return row["admin_revision"]

        raise HTTPException(422, f"unknown change kind {kind!r}")

    @staticmethod
    def reject_change(
        conn,
        *,
        change_id: UUID,
        actor_user_id: UUID | None,
        reason: str,
    ) -> dict:
        note = _required_text(reason, "reason")
        row = conn.execute(
            queries.get("get_course_change"), {"change_id": change_id}
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"course change {change_id} was not found")
        if row["status"] not in ("draft", "conflicted"):
            raise HTTPException(
                409, f"change status is '{row['status']}', expected draft or conflicted"
            )
        updated = conn.execute(
            queries.get("update_course_change_status"),
            {"change_id": change_id, "status": "rejected", "conflict_note": note},
        ).fetchone()
        conn.commit()
        return dict(updated)
