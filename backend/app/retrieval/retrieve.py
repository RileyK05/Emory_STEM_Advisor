"""Retrieval orchestration: run seams, fuse, contract-check, trace, refuse."""

import time
from dataclasses import replace
from uuid import UUID

from psycopg import Connection

from app.db import queries
from app.embeddings.base import Embedder
from app.retrieval.config import RetrievalConfig
from app.retrieval.fusion import FusedChunk, fuse
from app.retrieval.policy import program_reserve_cap
from app.retrieval.seams.embed import embed_seam
from app.retrieval.seams.keyword import keyword_seam
from app.retrieval.seams.prereq import (
    REVERSE_CITED_SET_SIZE,
    course_codes_in_query,
    is_reverse_query,
    prereq_seam,
)
from app.retrieval.trace import normalize_query, write_trace
from app.retrieval.types import (
    Candidate,
    CitedChunk,
    Refusal,
    RetrievalTrace,
    Retrieved,
    SeamResult,
)
from app.util.workspace import is_valid_workspace_key


class ContractViolation(RuntimeError):
    """A fused chunk lacked a locator/grounding. This is a bug, not data."""


def _add_timing(timings: dict[str, int] | None, name: str, started: float) -> None:
    if timings is not None:
        timings[name] = timings.get(name, 0) + int((time.perf_counter() - started) * 1000)


def _run(conn, name: str, func) -> SeamResult:
    """Run one seam inside a savepoint so a failure cannot poison the connection.

    A seam that raises a Python error is isolated by the catch, but a seam that
    trips a PostgreSQL statement error (timeout, invalid operation) leaves the
    shared transaction aborted unless its work is rolled back. A savepoint
    confines the rollback to that seam, so later seams, chunk-detail reads, and
    the trace write still run on the same connection. A remote embedder call is
    never made inside this block; the caller embeds first.
    """
    try:
        with conn.transaction():
            return func()
    except Exception as exc:  # noqa: BLE001 - a failing seam must not abort others
        return SeamResult(
            name=name,
            enabled=True,
            state="failed",
            reason=f"{type(exc).__name__}: {exc}",
        )


def _run_seams(conn, collection_id, query, embed_model, config, source_ids, workspace_key,
               embedder, *, keyword_off: str | None = None,
               timings: dict[str, int] | None = None) -> list[SeamResult]:
    """Run the keyword, embed, and prereq seams, each isolated so one failure cannot abort retrieval."""
    # The embed seam is always run (guarded), so it appears in the trace even
    # when disabled. The query is embedded before the first SQL read, on an idle
    # connection, so the remote embedder never runs inside a database
    # transaction; the seam then runs only its vector SQL inside a savepoint.
    # An embedder failure is isolated to the embed seam.
    embed_config = config.seams.embed
    query_vec = None
    embed_failure: str | None = None
    if embed_config.enabled and source_ids != [] and embedder is not None:
        started = time.perf_counter()
        try:
            query_vec = embedder.embed_query(query)
        except Exception as exc:  # noqa: BLE001 - an embedder failure stays in the embed seam
            embed_failure = f"{type(exc).__name__}: {exc}"
        _add_timing(timings, "embed_query_ms", started)
    if embed_failure is not None:
        embedding_result = SeamResult(
            name="embed", enabled=True, state="failed", reason=embed_failure
        )
    else:
        started = time.perf_counter()
        embedding_result = _run(conn, "embed", lambda: embed_seam(
            conn, collection_id, query, embed_model, embed_config,
            source_ids=source_ids, workspace_key=workspace_key, query_vec=query_vec,
        ))
        _add_timing(timings, "embed_ms", started)
    if keyword_off:
        keyword_result = SeamResult(
            name="keyword", enabled=True, state="dormant", reason=keyword_off)
    else:
        started = time.perf_counter()
        keyword_result = _run(conn, "keyword", lambda: keyword_seam(
            conn, collection_id, query, config.seams.keyword, source_ids=source_ids,
            workspace_key=workspace_key,
        ))
        _add_timing(timings, "keyword_ms", started)
    started = time.perf_counter()
    prereq_result = _run(conn, "prereq", lambda: prereq_seam(
        conn, collection_id, query, config.seams.prereq, source_ids=source_ids,
        workspace_key=workspace_key,
    ))
    _add_timing(timings, "prereq_ms", started)
    return [keyword_result, embedding_result, prereq_result]


def _cite(conn, fused: list[FusedChunk], collection_id, source_ids, workspace_key,
          dedupe: bool, details=None) -> tuple[list[CitedChunk], list[FusedChunk]]:
    """Attach each fused chunk's text and locator, checking the contract and dropping duplicates."""
    if details is None:
        details = {
            row["chunk_id"]: row
            for row in conn.execute(queries.get("cited_chunk_details_many"), {
                "chunk_ids": [item.chunk_id for item in fused], "workspace_key": workspace_key,
            }).fetchall()
        } if fused else {}
    cited: list[CitedChunk] = []
    contributions: list[FusedChunk] = []
    seen_text: set[str] = set()
    for item in fused:
        row = details.get(item.chunk_id)
        # Contract: every emitted chunk has a locator and a seam, and its
        # source is indexed. A violation is a pipeline bug.
        if (
            row is None
            or row["source_status"] != "indexed"
            or row["source_collection_id"] != collection_id
            or row["source_id"] != item.source_id
            or row["chunk_index"] != item.chunk_index
            or (source_ids is not None and row["source_id"] not in source_ids)
            or not item.seams
            or not row["locator_label"]
        ):
            raise ContractViolation(f"chunk {item.chunk_id} failed the contract check")
        # Duplicate passages (repeated boilerplate, overlapping chunks) are
        # cited once; the list is already score-ordered, so the first wins.
        if dedupe:
            fingerprint = row["text"].strip()
            if fingerprint in seen_text:
                continue
            seen_text.add(fingerprint)
        rank = len(cited) + 1
        contributions.append(replace(item, rank=rank))
        cited.append(
            CitedChunk(
                chunk_id=item.chunk_id,
                source_id=item.source_id,
                chunk_index=item.chunk_index,
                source_name=row["source_name"],
                locator_label=row["locator_label"],
                text=row["text"],
                seams=item.seams,
                normalized_score=item.normalized_score,
                source_slot=item.source_slot,
                rank=rank,
            )
        )
    return cited, contributions


def _alias_labels(conn, collection_id, cited: list[CitedChunk],
                  details) -> list[CitedChunk]:
    """Label a cross-listed course by all of its official codes.

    Only a group of two or more listings is relabelled, so ordinary courses keep
    their single code. The relation comes from stored catalog metadata, never
    from similar titles. If the group cannot be read, the original label stands.
    """
    canonical = {chunk.chunk_id: details[chunk.chunk_id].get("canonical_code")
                 for chunk in cited
                 if chunk.chunk_id in details and details[chunk.chunk_id].get("canonical_code")}
    if not canonical:
        return cited
    rows = conn.execute(queries.get("list_canonical_group_members"), {
        "collection_id": collection_id,
        "canonical_codes": sorted(set(canonical.values())),
    }).fetchall()
    groups = {row["canonical_code"]: row["codes"] for row in rows}
    relabelled = []
    for chunk in cited:
        codes = groups.get(canonical.get(chunk.chunk_id))
        label = " / ".join(codes) if codes and len(codes) > 1 else chunk.locator_label
        relabelled.append(replace(chunk, locator_label=label))
    return relabelled


def _merge_seams(runs: list[SeamResult]) -> list[SeamResult]:
    """One trace row per seam name: the union of that seam's runs in a planned query."""
    merged: dict[str, SeamResult] = {}
    for run in runs:
        prior = merged.get(run.name)
        if prior is None:
            merged[run.name] = run
            continue
        seen = {c.chunk_id for c in prior.candidates}
        candidates = prior.candidates + [c for c in run.candidates if c.chunk_id not in seen]
        states = {prior.state, run.state}
        state = next(s for s in ("active", "failed", "dormant", "disabled") if s in states)
        merged[run.name] = SeamResult(
            name=run.name, enabled=prior.enabled or run.enabled, state=state,
            candidates=candidates, reason=prior.reason if state == prior.state else run.reason)
    return list(merged.values())


def _planned(conn, collection_id, query, plan, embed_model, config: RetrievalConfig, source_ids,
             workspace_key, embedder,
             timings: dict[str, int] | None = None) -> tuple[list[SeamResult], list[FusedChunk]]:
    """Evidence chosen by a validated plan, in context order, before deduplication.

    The question keeps its semantic and exact-code searches; the raw prompt's
    OR-matched keywords (where generic words such as "major" and "courses" pull in
    unrelated documents) are replaced by the plan's focused searches. Planned
    course descriptions are read directly so a relevant course cannot be ranked
    away by an unrelated document's score range. Planned documents contribute
    their passages that best match the question, not their opening pages.
    """
    limits = config.planning

    def top(results, size):
        if is_reverse_query(query):
            size = max(size, REVERSE_CITED_SET_SIZE)
        return fuse(results, cited_set_size=size, embed_only_quota=config.seams.embed.only_quota,
                    min_score=config.fusion.min_score,
                    program_reserve_cap=program_reserve_cap(query, size))

    def seams(text, scope, keyword_off=None):
        return _run_seams(conn, collection_id, text, embed_model, config, scope, workspace_key,
                          embedder, keyword_off=keyword_off, timings=timings)

    question_runs = seams(query, source_ids, "replaced by planned searches")
    runs = list(question_runs)
    sections: list[list[tuple[FusedChunk, bool]]] = [
        [(item, False) for item in top(question_runs, limits.question_slots)]]
    # A planner's per-search/page quota must not split the exact program the
    # question names. Keep only its bound evidence from the raw keyword run;
    # generic raw-prompt keyword hits remain replaced by the planned searches.
    named = _run(conn, "keyword", lambda: keyword_seam(
        conn, collection_id, query, config.seams.keyword,
        source_ids=source_ids, workspace_key=workspace_key))
    exact = [candidate for candidate in named.candidates if candidate.program_evidence]
    if exact:
        runs.append(replace(named, candidates=exact))
        sections.insert(0, [(FusedChunk(c.chunk_id, c.source_id, c.chunk_index,
                                       ("keyword",), 1., 0, 0), False) for c in exact])

    planned_courses: list[Candidate] = []
    for code in plan.course_codes:
        found = _run(conn, "plan", lambda code=code: SeamResult(
            name="plan", enabled=True, state="active", candidates=[
                Candidate(row["chunk_id"], row["source_id"], row["chunk_index"], "plan", 1.0)
                for row in conn.execute(queries.get("prereq_self_chunks"), {
                    "collection_id": collection_id, "code": code, "source_ids": source_ids,
                    "workspace_key": workspace_key}).fetchall()]))
        planned_courses.extend(found.candidates)
    sections.append([(FusedChunk(c.chunk_id, c.source_id, c.chunk_index, ("plan",), 1.0, 0, 0),
                      True) for c in planned_courses])

    documents = [source for source in plan.document_sources
                 if source_ids is None or source in source_ids]
    if documents:
        document_runs = seams(query, documents)[:2]
        runs.extend(document_runs)
        sections.append([(item, True) for item in
                         top(document_runs, limits.document_chunks * len(documents))])

    per_query = []
    for text in plan.search_queries:
        search_runs = seams(text, source_ids)[:2]
        runs.extend(search_runs)
        per_query.append(top(search_runs, limits.search_slots))
    # Round-robin, so the last planned search is not the first one cut.
    sections.append([(hits[rank], True) for rank in range(limits.search_slots)
                     for hits in per_query if rank < len(hits)])

    plan_candidates = list(planned_courses)
    seen_plan = {candidate.chunk_id for candidate in plan_candidates}
    for section in sections[1:]:
        for item, from_plan in section:
            if from_plan and item.chunk_id not in seen_plan:
                seen_plan.add(item.chunk_id)
                plan_candidates.append(Candidate(item.chunk_id, item.source_id,
                                                 item.chunk_index, "plan", 1.0))
    runs.append(SeamResult(
        name="plan", enabled=True,
        state="active" if plan_candidates else "dormant",
        candidates=plan_candidates, reason=plan.summary()))
    ordered: dict[UUID, FusedChunk] = {}
    for section in sections:
        for item, from_plan in section:
            prior = ordered.get(item.chunk_id)
            tags = set(item.seams) | ({"plan"} if from_plan else set())
            if prior is None:
                ordered[item.chunk_id] = replace(item, seams=tuple(sorted(tags)))
            else:
                ordered[item.chunk_id] = replace(
                    prior, seams=tuple(sorted(tags | set(prior.seams))),
                    normalized_score=max(item.normalized_score, prior.normalized_score))
    return _merge_seams(runs), list(ordered.values())


def _limit_planned(fused: list[FusedChunk], details, size: int) -> list[FusedChunk]:
    """Drop repeated passages and cross-listed aliases, cap, and number sources."""
    kept: list[FusedChunk] = []
    seen_text: set[str] = set()
    seen_canonical: set[str] = set()
    slots: dict[UUID, int] = {}
    for item in fused:
        row = details.get(item.chunk_id)
        if row is not None:
            fingerprint, canonical = row["text"].strip(), row.get("canonical_code")
            if fingerprint in seen_text or (canonical and canonical in seen_canonical):
                continue
            seen_text.add(fingerprint)
            if canonical:
                seen_canonical.add(canonical)
        slots.setdefault(item.source_id, len(slots) + 1)
        kept.append(replace(item, source_slot=slots[item.source_id], rank=len(kept) + 1))
        if len(kept) == size:
            break
    return kept


def retrieve(
    conn: Connection,
    *,
    collection_id: UUID,
    query: str,
    embedder: Embedder,
    embed_model: str,
    config: RetrievalConfig,
    source_ids: list[UUID] | None = None,
    workspace_key: str | None = None,
    plan=None,
    trace_id: UUID | None = None,
) -> Retrieved | Refusal:
    """Retrieve cited context. `plan` is a validated `QueryPlan` from the query
    planner; `trace_id` lets a planned query share its operation's identity."""
    if workspace_key is not None and not is_valid_workspace_key(workspace_key):
        raise ValueError("workspace_key must contain 1-200 characters")
    started = time.perf_counter()
    timings: dict[str, int] = {}
    if plan is not None and not plan.empty:
        planned_started = time.perf_counter()
        results, planned = _planned(conn, collection_id, query, plan, embed_model, config,
                                    source_ids, workspace_key, embedder, timings=timings)
        _add_timing(timings, "planned_ms", planned_started)
        details_started = time.perf_counter()
        details = {
            row["chunk_id"]: row
            for row in conn.execute(queries.get("cited_chunk_details_many"), {
                "chunk_ids": [item.chunk_id for item in planned], "workspace_key": workspace_key,
            }).fetchall()
        } if planned else {}
        _add_timing(timings, "details_ms", details_started)
        limit_started = time.perf_counter()
        fused = _limit_planned(planned, details, config.planning.context_size)
        _add_timing(timings, "limit_ms", limit_started)
        return _finish(conn, collection_id, query, embed_model, config, source_ids, workspace_key,
                       results, fused, details, trace_id, timings, started)
    seams_started = time.perf_counter()
    results = _run_seams(conn, collection_id, query, embed_model, config, source_ids,
                         workspace_key, embedder, timings=timings)
    _add_timing(timings, "seams_wall_ms", seams_started)
    details = None
    if config.fusion.dedupe:
        details_started = time.perf_counter()
        chunk_ids = sorted({c.chunk_id for result in results for c in result.candidates}, key=str)
        details = {
            row["chunk_id"]: row
            for row in conn.execute(queries.get("cited_chunk_details_many"), {
                "chunk_ids": chunk_ids, "workspace_key": workspace_key,
            }).fetchall()
        } if chunk_ids else {}
        _add_timing(timings, "details_ms", details_started)
    cited_set_size = (REVERSE_CITED_SET_SIZE if is_reverse_query(query)
                      else config.fusion.cited_set_size)
    fusion_started = time.perf_counter()
    fused = fuse(
        results,
        cited_set_size=cited_set_size,
        embed_only_quota=config.seams.embed.only_quota,
        min_score=config.fusion.min_score,
        chunk_texts={cid: row["text"] for cid, row in details.items()}
        if details is not None else None,
        canonical_keys={
            cid: row["canonical_code"]
            for cid, row in (details or {}).items() if row.get("canonical_code")
        } if details is not None else None,
        program_reserve_cap=program_reserve_cap(query, cited_set_size),
    )
    _add_timing(timings, "fusion_ms", fusion_started)
    return _finish(conn, collection_id, query, embed_model, config, source_ids, workspace_key,
                   results, fused, details, trace_id, timings, started)


def _finish(conn, collection_id, query, embed_model, config, source_ids, workspace_key,
            results, fused, details, trace_id, timings: dict[str, int] | None = None,
            started: float | None = None) -> Retrieved | Refusal:
    held = set()
    held_codes: set[str] = set()
    if fused:
        held_started = time.perf_counter()
        held_rows = conn.execute(
            queries.get("held_atlas_context_chunks"), {
                "chunk_ids": [item.chunk_id for item in fused],
                "collection_id": collection_id,
            },
        ).fetchall()
        held = {row["chunk_id"] for row in held_rows}
        held_codes = {row["course_code"] for row in held_rows}
        fused = [item for item in fused if item.chunk_id not in held]
        _add_timing(timings, "held_ms", held_started)

    cite_started = time.perf_counter()
    cited, contributions = _cite(conn, fused, collection_id, source_ids, workspace_key,
                                 config.fusion.dedupe, details)
    if details:
        cited = _alias_labels(conn, collection_id, cited, details)
    _add_timing(timings, "cite_ms", cite_started)

    asked_held_codes = sorted(held_codes & set(course_codes_in_query(query)))
    refused = not cited or bool(asked_held_codes)
    if timings is not None and started is not None:
        timings["total_ms"] = int((time.perf_counter() - started) * 1000)
    trace_started = time.perf_counter()
    trace_id = write_trace(
        conn,
        trace_id=trace_id,
        collection_id=collection_id,
        query=query,
        config_version=config.version,
        embed_model=embed_model,
        refused=refused,
        seams=results,
        contributions=contributions,
        workspace_key=workspace_key,
        timings=timings,
    )
    _add_timing(timings, "trace_write_ms", trace_started)
    trace = RetrievalTrace(
        trace_id=trace_id,
        collection_id=collection_id,
        query=query,
        normalized_query=normalize_query(query),
        config_version=config.version,
        embed_model=embed_model,
        refused=refused,
        seams=tuple(results),
        warnings=(
            [
                (
                    f"Withheld {len(held)} master course excerpt(s) pending source "
                    f"conflict review for {', '.join(sorted(held_codes))}."
                )
            ]
            if held else []),
        timings=dict(timings or {}),
    )
    if refused:
        if asked_held_codes:
            reason = (
                "The requirement for " + ", ".join(asked_held_codes)
                + " is under review because source evidence conflicts. "
                "Confirm it with an advisor before relying on it."
            )
        elif held:
            reason = ("relevant course requirements are awaiting source conflict review")
        else:
            reason = "no relevant material found"
        return Refusal(reason=reason, trace=trace)
    return Retrieved(chunks=cited, trace=trace)
