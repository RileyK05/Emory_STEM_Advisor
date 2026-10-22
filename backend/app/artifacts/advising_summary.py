"""Deterministic advising summary and plan export (Item 5).

Generates a structured, evidence-linked DocumentSpec from published conversation
turns. Binds:
- Student inquiries and stated academic goals.
- Model guidance and recommendations.
- Cited institutional evidence and passage excerpts.
- Open questions, unresolved conditions, and verification blockers.
- Selected artifact/plan revisions.

Crucial invariant: Unsupported advice cannot become a verified plan by export.
Unresolved conditions remain explicit blockers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.artifacts.models import (
    ArtifactReference,
    DocumentBlock,
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)
from app.artifacts.storage import ArtifactStore
from app.db import queries
from app.util.digest import json_digest


def build_advising_summary_spec(
    conn,
    conversation_id: UUID,
    workspace_key: str,
    *,
    through_sequence: int | None = None,
    title: str | None = None,
    referenced_artifacts: list[dict[str, Any]] | None = None,
) -> DocumentSpec:
    """Build a deterministic DocumentSpec pinning published conversation turns."""
    conv = conn.execute(queries.get("get_conversation"), {
        "conversation_id": conversation_id,
        "workspace_key": workspace_key,
    }).fetchone()
    if conv is None:
        raise HTTPException(404, "Conversation not found.")

    pinned_artifacts = []
    for reference in referenced_artifacts or []:
        try:
            artifact_id = UUID(str(reference["artifactId"]))
            revision_id = UUID(str(reference["revisionId"]))
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, "artifact references require exact artifact/revision IDs") from exc
        artifact = conn.execute(queries.get("get_artifact_revision"), {
            "artifact_id": artifact_id, "revision_id": revision_id, "workspace_key": workspace_key,
        }).fetchone()
        if artifact is None:
            raise HTTPException(404, "Referenced artifact revision not found.")
        pinned_artifacts.append({
            "artifactId": str(artifact_id), "revisionId": str(revision_id),
            "title": artifact["spec"]["title"], "kind": artifact["kind"],
        })

    rows = conn.execute(queries.get("get_published_conversation_turns"), {
        "conversation_id": conversation_id,
        "through_sequence": through_sequence,
    }).fetchall()

    if not rows:
        raise HTTPException(422, "Conversation has no published turns to summarize.")

    max_sequence = max(r["sequence"] for r in rows)
    user_turns = [r for r in rows if r["role"] == "user"]
    assistant_turns = [r for r in rows if r["role"] == "assistant"]

    doc_title = title.strip() if title and title.strip() else f"Advising Summary: {conv['title']}"
    blocks: list[DocumentBlock] = []
    evidence_map: dict[str, list[ArtifactReference]] = {}

    # 1. Document Title & Header
    blocks.append(HeadingBlock(id="b_title", level=1, text=doc_title))
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    blocks.append(ParagraphBlock(
        id="b_meta",
        text=f"Advising conversation summary through turn {max_sequence}. Exported {timestamp}. "
             "Recommendations retain their original evidence and uncertainty; this export is not an institutional approval."
    ))

    # 2. Student Goals and Inquiries
    blocks.append(HeadingBlock(id="b_goals_head", level=2, text="Student Goals & Inquiries"))
    user_items = [
        f"Turn {turn['sequence']}: {turn['text'].replace('\n', ' ').strip()}"
        for turn in user_turns
        if turn["text"].strip()
    ]
    if user_items:
        blocks.append(ListBlock(id="b_goals_list", ordered=True, items=user_items))
    else:
        blocks.append(ParagraphBlock(id="b_goals_none", text="No explicit user inquiries recorded."))

    # 3. Advising Guidance & Recommendations
    blocks.append(HeadingBlock(id="b_advice_head", level=2, text="Advising Guidance & Recommendations"))
    all_cited_chunks: dict[str, ArtifactReference] = {}
    citation_labels: dict[str, list[str]] = {}
    open_questions: list[str] = []

    turn_counter = 0
    for turn in assistant_turns:
        turn_counter += 1
        seq = turn["sequence"]
        turn_evidence = turn.get("evidence") or {}

        blocks.append(HeadingBlock(
            id=f"b_turn_{seq}_head",
            level=3,
            text=f"Guidance (Turn {seq})"
        ))

        p_id = f"b_turn_{seq}_text"
        blocks.append(ParagraphBlock(id=p_id, text=turn["text"].strip()))

        # Collect cited passages for this turn
        context_block = turn_evidence.get("contextBlock") or []
        context_by_id = {str(item.get("chunkId")): item for item in context_block}
        citations = turn_evidence.get("citations") or []
        turn_refs: list[ArtifactReference] = []

        for number, citation in enumerate(citations, 1):
            item = context_by_id.get(str(citation.get("chunkId")), {})
            try:
                if not item.get("text"):
                    raise ValueError("saved cited passage is missing")
                ref = ArtifactReference(
                    chunk_id=UUID(str(item.get("chunkId"))),
                    source_id=UUID(str(item.get("sourceId"))),
                    source_name=str(item.get("sourceName") or "Source"),
                    locator_label=str(item.get("locatorLabel") or "Section"),
                    text_snapshot=str(item.get("text") or ""),
                )
                turn_refs.append(ref)
                all_cited_chunks[str(ref.chunk_id)] = ref
                citation_labels.setdefault(str(ref.chunk_id), []).append(f"Turn {seq} [{number}]")
            except (ValueError, TypeError):
                open_questions.append(f"Turn {seq} citation [{number}] has no complete saved passage; verify its support.")
                continue

        if turn_refs:
            evidence_map[p_id] = turn_refs

        # Research findings / judgments
        research = turn_evidence.get("research")
        if isinstance(research, dict):
            findings = []
            for b in research.get("blocks", []):
                kind = b.get("kind")
                text = b.get("text", "").replace("\n", " ").strip()
                if kind in {"fact", "judgment"} and text:
                    findings.append(f"[{kind.capitalize()}] {text}")
            if findings:
                blocks.append(ParagraphBlock(
                    id=f"b_turn_{seq}_findings_intro",
                    text="Research findings and recommendations:"
                ))
                blocks.append(ListBlock(id=f"b_turn_{seq}_findings", ordered=False, items=findings))
                if turn_refs:
                    evidence_map[f"b_turn_{seq}_findings"] = turn_refs

            unresolved = research.get("unresolvedQuestions") or []
            for u in unresolved:
                clean_u = str(u).replace("\n", " ").strip()
                if clean_u and clean_u not in open_questions:
                    open_questions.append(clean_u)

        # Applicability notes
        applicability = turn_evidence.get("applicability")
        if isinstance(applicability, dict):
            headline = applicability.get("headline")
            level = applicability.get("level", "info")
            if headline:
                blocks.append(ParagraphBlock(
                    id=f"b_turn_{seq}_app",
                    text=f"Applicability Notice ({level.upper()}): {headline.strip()}"
                ))
            conflicts = applicability.get("conflicts") or []
            for c in conflicts:
                clean_c = str(c).replace("\n", " ").strip()
                if clean_c and clean_c not in open_questions:
                    open_questions.append(clean_c)

    # 4. Open Questions & Verification Blockers
    blocks.append(HeadingBlock(id="b_open_head", level=2, text="Open Questions & Verification Blockers"))
    if open_questions:
        blocks.append(ParagraphBlock(
            id="b_open_intro",
            text="The following requirements, prerequisite conditions, or scope questions remain open and must be verified before acting on this plan:"
        ))
        blocks.append(ListBlock(id="b_open_list", ordered=False, items=open_questions))
    else:
        blocks.append(ParagraphBlock(
            id="b_open_none",
            text="No open questions were recorded in the included turns. This does not verify program or degree completion."
        ))

    # 5. Verified Sources & Citations Table
    blocks.append(HeadingBlock(id="b_sources_head", level=2, text="Referenced Sources"))
    if all_cited_chunks:
        headers = ["Source Document", "Locator / Section", "Cited Text Excerpt", "Citation in conversation"]
        source_table_rows: list[list[str]] = []
        for ref in all_cited_chunks.values():
            excerpt = ref.text_snapshot.replace("\n", " ").strip()
            if len(excerpt) > 200:
                excerpt = excerpt[:197] + "..."
            source_table_rows.append([ref.source_name, ref.locator_label, excerpt,
                         ", ".join(citation_labels[str(ref.chunk_id)])])
        blocks.append(TableBlock(id="b_sources_table", headers=headers, rows=source_table_rows))
    else:
        blocks.append(ParagraphBlock(
            id="b_sources_none",
            text="No source passages were available in the saved evidence for these turns."
        ))

    # 6. Referenced Artifacts and Degree Plans (if any provided)
    if pinned_artifacts:
        blocks.append(HeadingBlock(id="b_artifacts_head", level=2, text="Referenced Artifacts & Degree Plans"))
        headers = ["Artifact Title", "Type", "Revision ID"]
        art_rows = [
            [
                str(art.get("title") or "Untitled"),
                str(art.get("kind") or "document"),
                str(art.get("revisionId") or "latest"),
            ]
            for art in pinned_artifacts
        ]
        blocks.append(TableBlock(id="b_artifacts_table", headers=headers, rows=art_rows))

    provenance = {
        "conversation_id": str(conversation_id),
        "through_sequence": max_sequence,
        "exported_at": timestamp,
        "type": "advising_summary",
        "artifact_revisions": json.dumps(pinned_artifacts, sort_keys=True),
        "snapshot_digest": json_digest({
            "conversation_id": str(conversation_id), "through_sequence": max_sequence,
            "turns": [{"sequence": r["sequence"], "text": r["text"], "evidence": r.get("evidence")}
                      for r in rows], "artifacts": pinned_artifacts,
        }),
    }

    return DocumentSpec(
        title=doc_title,
        blocks=blocks,
        evidence=evidence_map,
        provenance=provenance,
    )


def create_advising_summary_artifact(
    conn,
    settings,
    workspace_key: str,
    conversation_id: UUID,
    *,
    through_sequence: int | None = None,
    title: str | None = None,
    referenced_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build and persist the advising summary document as an immutable artifact revision."""
    spec = build_advising_summary_spec(
        conn,
        conversation_id,
        workspace_key,
        through_sequence=through_sequence,
        title=title,
        referenced_artifacts=referenced_artifacts,
    )
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        from pathlib import Path
        data_dir = Path(settings) if isinstance(settings, (str, Path)) else Path("data")
    store = ArtifactStore(conn, workspace_key=workspace_key, data_dir=data_dir)
    record = store.create(spec)
    conn.commit()
    return record
