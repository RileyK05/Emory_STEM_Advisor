"""Immutable prompt versions. Promotion stays blocked until a human review and a fixture exist.

Generation reads the newest promoted version of a prompt by name (`promoted`);
without one it uses the prompt built into the code.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.auth.capabilities import require_operation
from app.auth.models import Principal
from app.db import queries
from app.util.digest import json_digest


class PromptRegistry:
    @staticmethod
    def promoted(conn, name: str):
        """(template text, recorded prompt version) of the live promotion, or None."""
        row = conn.execute(queries.get("get_promoted_prompt"), {"name": name}).fetchone()
        if row is None:
            return None
        return row["template_text"], f"registry-{row['content_digest'][:16]}"

    @staticmethod
    def _reviewer(actor: Principal) -> None:
        require_operation(actor, "sources.review")

    @classmethod
    def register(
        cls,
        conn,
        actor: Principal,
        *,
        name: str,
        template_text: str,
        version_id: UUID | None = None,
    ) -> dict[str, Any]:
        cls._reviewer(actor)
        text = template_text.strip()
        label = name.strip()
        if not label or not text:
            raise HTTPException(422, "prompt name and text are required")
        digest = json_digest({"template": text})
        existing = conn.execute(
            queries.get("get_prompt_version_by_digest"),
            {"content_digest": digest},
        ).fetchone()
        if version_id is not None:
            named = conn.execute(
                queries.get("get_prompt_version"),
                {"version_id": version_id},
            ).fetchone()
            if named is not None and named["content_digest"] != digest:
                raise HTTPException(409, "prompt text cannot reuse this version identity")
        if existing is not None:
            return dict(existing)
        row = conn.execute(queries.get("insert_prompt_version"), {
            "version_id": uuid4(),
            "name": label,
            "template_text": text,
            "content_digest": digest,
        }).fetchone()
        return dict(row)

    @classmethod
    def approve(cls, conn, actor: Principal, version_id: UUID) -> dict[str, Any]:
        cls._reviewer(actor)
        row = conn.execute(
            queries.get("approve_prompt_version"),
            {"version_id": version_id},
        ).fetchone()
        if row is None:
            current = conn.execute(
                queries.get("get_prompt_version"),
                {"version_id": version_id},
            ).fetchone()
            if current is not None and current["review_state"] == "approved":
                return dict(current)
            raise HTTPException(404, "prompt version not found")
        return dict(row)

    @classmethod
    def put_fixture(
        cls,
        conn,
        actor: Principal,
        *,
        suite_name: str,
        fixture_text: str,
    ) -> dict[str, Any]:
        cls._reviewer(actor)
        suite = suite_name.strip()
        text = fixture_text.strip()
        if not suite or not text:
            raise HTTPException(422, "suite name and fixture text are required")
        digest = json_digest({"fixture": text, "suite": suite})
        current = conn.execute(
            queries.get("get_current_eval_fixture"),
            {"suite_name": suite},
        ).fetchone()
        if current is not None and current["fixture_digest"] == digest:
            return dict(current)
        conn.execute(queries.get("clear_current_eval_fixture"), {"suite_name": suite})
        row = conn.execute(queries.get("insert_eval_fixture"), {
            "fixture_id": uuid4(),
            "suite_name": suite,
            "fixture_digest": digest,
            "fixture_text": text,
        }).fetchone()
        return dict(row)

    @classmethod
    def promote(
        cls,
        conn,
        actor: Principal,
        *,
        version_id: UUID,
        suite_name: str,
    ) -> dict[str, Any]:
        cls._reviewer(actor)
        version = conn.execute(
            queries.get("get_prompt_version"),
            {"version_id": version_id},
        ).fetchone()
        if version is None:
            raise HTTPException(404, "prompt version not found")
        if version["review_state"] != "approved":
            raise HTTPException(409, "prompt review is not approved")
        suite = suite_name.strip()
        fixture = conn.execute(
            queries.get("get_current_eval_fixture"),
            {"suite_name": suite},
        ).fetchone()
        if fixture is None or not fixture["present"]:
            raise HTTPException(409, "eval fixture is missing")
        existing = conn.execute(queries.get("get_prompt_promotion"), {
            "version_id": version_id,
            "suite_name": suite,
            "fixture_digest": fixture["fixture_digest"],
        }).fetchone()
        if existing is not None:
            return dict(existing)
        row = conn.execute(queries.get("insert_prompt_promotion"), {
            "promotion_id": uuid4(),
            "version_id": version_id,
            "suite_name": suite,
            "fixture_digest": fixture["fixture_digest"],
        }).fetchone()
        return dict(row)
