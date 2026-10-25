"""End-to-end advising tools over an imported atlas and real retrieval context."""

import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.advising.catalog import _requirements_are_cited, load_advising_context
from app.advising.requirement_holds import apply_requirement_hold, release_requirement_hold
from app.advising.sequence import CourseRequirement, plan_sequence
from app.api import create_app
from app.atlas.importer import import_atlas
from app.config import load_settings
from app.db.connection import connection_dsn
from app.llm.base import GenerationResult, ToolCall, ToolResultMessage, Usage
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import retrieve
from app.retrieval.types import CitedChunk, Refusal, Retrieved
from app.simgraph.build import build_simgraph
from tests.fakes import whitespace_tokens
from tests.helpers import collection_name, lenient_config

_MODEL = "fake-advising-model"
_COURSES = ("CS 110", "CS 120", "CS 220", "CS 230")


class _PlantedAtlasEmbedder:
    """Put the first pair and second pair into distinct deterministic topics."""

    dim = 8

    def _vector(self, text):
        vector = np.zeros(self.dim, dtype=np.float32)
        vector[0 if "CS 110" in text or "CS 120" in text else 1] = 1.0
        return vector

    def embed_documents(self, texts):
        return np.vstack([self._vector(text) for text in texts]).astype(np.float32)

    def embed_query(self, text):
        return self._vector(text)


class _AdvisingProvider:
    """Issue all three registered tools, using only resource IDs in the prompt."""

    _resource_pattern = re.compile(r"resource_id=([0-9a-f-]{36})")
    _number_pattern = re.compile(r"\[(\d+)\].*?resource_id=([0-9a-f-]{36})")

    def __init__(self):
        self.requests = []
        self.resource_ids = []
        self.citation_by_resource = {}

    def generate(self, request):
        self.requests.append(request)
        usage = Usage(100, 10, source="provider")
        call_index = len(self.requests)
        if call_index == 1:
            prompt = request.messages[1]["content"]
            self.resource_ids = list(dict.fromkeys(self._resource_pattern.findall(prompt)))
            self.citation_by_resource = {
                resource: int(number)
                for number, resource in self._number_pattern.findall(prompt)
            }
            return GenerationResult(
                tool_calls=(ToolCall("topics", "advisor_explore_topics", {
                    "resource_ids": self.resource_ids,
                }),),
                finish_reason="tool_calls", usage=usage,
            )
        if call_index == 2:
            return GenerationResult(
                tool_calls=(ToolCall("requirements", "advisor_read_course_requirements", {
                    "resource_ids": self.resource_ids, "course_codes": list(_COURSES),
                }),),
                finish_reason="tool_calls", usage=usage,
            )
        if call_index == 3:
            return GenerationResult(
                tool_calls=(ToolCall("sequence", "advisor_plan_course_sequence", {
                    "resource_ids": self.resource_ids,
                    "selected_codes": ["CS 110", "CS 120", "CS 220"],
                    "completed_codes": [],
                }),),
                finish_reason="tool_calls", usage=usage,
            )
        if call_index == 4:
            results = [
                json.loads(message.content)
                for message in request.messages
                if isinstance(message, ToolResultMessage)
            ]
            sequence = results[-1]
            evidence = {item["code"]: item["citation_numbers"] for item in sequence["evidence"]}
            stage_codes = [code for stage in sequence["stages"] for code in stage]
            citations = sorted({
                number for code in stage_codes for number in evidence.get(code, [])
            })
            answer = (
                "Dependency order: " + " then ".join(stage_codes)
                + " " + " ".join(f"[{number}]" for number in citations) + "."
            )
            return GenerationResult(text=answer, finish_reason="stop", usage=usage)
        raise AssertionError("query made an unexpected provider call")


def _api_client(db, provider, config=None):
    settings = replace(
        load_settings(),
        llm_provider="openai",
        llm_model_id="offline-advising-tools",
        tools_enabled=True,
        operation_max_calls=8,
        database_url=connection_dsn(db),
    )
    app = create_app(
        settings=settings,
        allow_test_client=True,
        config=config or lenient_config(),
        embedder=_PlantedAtlasEmbedder(),
        provider=provider,
    )
    app.state.override_conn = db
    return TestClient(app)


def _import_atlas(db, collection, tmp_path):
    path = tmp_path / "advising-atlas.jsonl"
    path.write_text("\n".join([
        json.dumps({
            "code": "CS 110", "title": "Programming Foundations",
            "description": "Foundational coding and variables.", "requisites": None,
        }),
        json.dumps({
            "code": "CS 120", "title": "Data Structures",
            "description": "Programming data structures and collections.",
            "requisites": "Prerequisite: CS 110",
        }),
        json.dumps({
            "code": "CS 220", "title": "Algorithms",
            "description": "Algorithms and their analysis.",
            "requisites": "Prerequisite: CS 120",
        }),
        json.dumps({
            "code": "CS 230", "title": "Computer Systems",
            "description": "Computer systems and machine architecture.", "requisites": None,
        }),
    ]), encoding="utf-8")
    config = load_retrieval_config()
    source_id = import_atlas(
        db,
        collection_id=collection,
        path=path,
        embedder=_PlantedAtlasEmbedder(),
        embed_model=_MODEL,
        count_tokens=whitespace_tokens,
        config=config,
    )
    build_simgraph(
        db,
        collection_id=collection,
        model=_MODEL,
        config_version=config.version,
        simgraph=config.simgraph,
    )
    db.commit()
    return source_id


def test_description_only_requirements_block_sequence_without_inventing_edges(db, collection, tmp_path):
    from app.db import queries

    path = tmp_path / "description-requirements.jsonl"
    path.write_text(json.dumps({
        "code": "CS 220", "title": "Algorithms",
        "description": "Prerequisite: CS 120. Algorithm analysis.", "requisites": None,
    }), encoding="utf-8")
    import_atlas(db, collection_id=collection, path=path,
        embedder=_PlantedAtlasEmbedder(), embed_model=_MODEL,
        count_tokens=whitespace_tokens, config=load_retrieval_config())
    # Simulate an atlas indexed before the description fallback existed. Reads
    # must still block this old metadata rather than writing graph nodes.
    db.execute("UPDATE courses SET requisites_raw = NULL, parse_status = 'none' "
               "WHERE collection_id = %(c)s", {"c": collection})
    db.commit()
    rows = db.execute(queries.get("diagnostic_current_chunks"), {
        "collection_id": collection, "workspace_key": None, "model": _MODEL,
    }).fetchall()
    chunks = [CitedChunk(r["chunk_id"], r["source_id"], r["chunk_index"], r["source_name"],
        r["locator_label"], r["text"], ("keyword",), 1., 1, i + 1) for i, r in enumerate(rows)]
    before = _prerequisite_snapshot(db, collection)
    context = load_advising_context(db, collection_id=collection, chunks=chunks)
    course = context["courses"][0]
    assert course["stored_parse_status"] == "none"
    assert course["parse_status"] == "unparsed"
    assert course["blocker_reason"] == "requirements_evidence_missing"
    assert course["prerequisites"] is None
    # Metadata must still block if retrieval selected a later description fragment.
    later = [replace(chunk, text="Algorithm analysis.") for chunk in chunks]
    assert load_advising_context(db, collection_id=collection, chunks=later)["courses"][0][
        "parse_status"] == "unparsed"
    assert _prerequisite_snapshot(db, collection) == before


def _prerequisite_snapshot(db, collection):
    return (
        db.execute(
            "SELECT course_id, source_id, code, requisites_raw, parse_status "
            "FROM courses WHERE collection_id = %(collection_id)s ORDER BY code",
            {"collection_id": collection},
        ).fetchall(),
        db.execute(
            "SELECT m.course_id, m.kind, m.target_code, m.target_course_id, m.position "
            "FROM prereq_mentions m JOIN courses c USING (course_id) "
            "WHERE c.collection_id = %(collection_id)s ORDER BY m.course_id, m.kind, m.position",
            {"collection_id": collection},
        ).fetchall(),
        db.execute(
            "SELECT n.course_id, n.node_id, n.kind, n.parent_node_id, n.position, "
            "n.node_type, n.target_code, n.condition_text "
            "FROM prereq_nodes n JOIN courses c USING (course_id) "
            "WHERE c.collection_id = %(collection_id)s ORDER BY n.course_id, n.kind, n.position",
            {"collection_id": collection},
        ).fetchall(),
    )


def test_source_scoped_requirement_hold_keeps_evidence_and_blocks_verified_graph(db, collection, tmp_path):
    from app.db import queries

    path = tmp_path / "conflicted-atlas.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in (
        {"code": "CS 171", "title": "Introduction to Computer Science II",
         "description": "Algorithms.", "requisites": "Prerequisite: MUS 221"},
        {"code": "CS 172", "title": "Later course", "description": "Later algorithms.",
         "requisites": "Prerequisite: CS 171"},
        {"code": "MUS 221", "title": "Theory", "description": "Music theory.",
         "requisites": None},
    )), encoding="utf-8")
    source_id = import_atlas(
        db, collection_id=collection, path=path,
        embedder=_PlantedAtlasEmbedder(), embed_model=_MODEL,
        count_tokens=whitespace_tokens, config=load_retrieval_config(),
    )
    master_hash = db.execute(
        "SELECT file_hash FROM sources WHERE source_id = %(source_id)s",
        {"source_id": source_id},
    ).fetchone()["file_hash"]
    master_course = db.execute(
        "SELECT course_id FROM courses WHERE source_id = %(source_id)s AND code = 'CS 171'",
        {"source_id": source_id},
    ).fetchone()
    before = _prerequisite_snapshot(db, collection)
    capture_path = tmp_path / "spring-2027-capture.json"
    capture_path.write_bytes(b"saved live Atlas evidence for CS 171")
    capture_hash = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    relative_capture_path = os.path.relpath(capture_path, Path.cwd())
    index_revision = db.execute(
        "SELECT index_revision FROM sources WHERE source_id = %(source_id)s",
        {"source_id": source_id},
    ).fetchone()["index_revision"]
    capture_set_hash = hashlib.sha256(b"linked offering capture set").hexdigest()
    captured_at = datetime.now(UTC)
    db.execute(queries.get("insert_atlas_offering_snapshot"), {
        "source_id": source_id, "capture_set_hash": capture_set_hash,
        "career": "Undergraduate", "term": "Spring 2027",
        "index_evidence_hash": "d" * 64, "announced_count": 1,
        "result_row_count": 1, "eligible_row_count": 1, "held_row_count": 0,
        "search_criteria": "Term: Spring 2027; Career: Undergraduate",
        "capture_hashes": Jsonb([capture_hash]),
        "observed_from": captured_at, "observed_until": captured_at,
    })
    with pytest.raises(ValueError, match="master file hash"):
        apply_requirement_hold(
            db, source_id=source_id, course_code="CS 171", master_file_hash="b" * 64,
            conflict_reason="wrong source hash", evidence_capture_sha256=capture_hash,
            evidence_capture_path=str(capture_path), curator="test curator",
        )
    with pytest.raises(ValueError, match="does not match the saved capture"):
        apply_requirement_hold(
            db, source_id=source_id, course_code="CS 171", master_file_hash=master_hash,
            conflict_reason="wrong capture digest", evidence_capture_sha256="c" * 64,
            evidence_capture_path=str(capture_path), curator="test curator",
        )
    other_capture_path = tmp_path / "other-capture.json"
    other_capture_path.write_bytes(b"different saved capture")
    other_capture_hash = hashlib.sha256(other_capture_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="not part of the exact offering capture set"):
        apply_requirement_hold(
            db, source_id=source_id, course_code="CS 171", master_file_hash=master_hash,
            conflict_reason="capture is outside the pinned offering set",
            evidence_capture_sha256=other_capture_hash,
            evidence_capture_path=str(other_capture_path), curator="test curator",
            evidence_source_id=source_id, evidence_index_revision=index_revision,
            evidence_capture_set_hash=capture_set_hash,
        )
    hold = apply_requirement_hold(
        db, source_id=source_id, course_code="CS 171", master_file_hash=master_hash,
        conflict_reason="Spring 2027 offering says CS 170 or CS_OX 170; master says MUS 221.",
        evidence_capture_sha256=capture_hash,
        evidence_capture_path=relative_capture_path,
        curator="test curator",
        evidence_source_id=source_id, evidence_index_revision=index_revision,
        evidence_capture_set_hash=capture_set_hash,
    )
    assert hold["course_code"] == "CS 171"
    assert Path(hold["evidence_capture_path"]).is_absolute()
    saved_capture = Path(hold["evidence_capture_path"])
    assert saved_capture.is_relative_to(load_settings().data_dir / "raw" / "holds")
    assert saved_capture.read_bytes() == capture_path.read_bytes()
    assert _prerequisite_snapshot(db, collection) == before

    chunk_rows = db.execute(queries.get("diagnostic_current_chunks"), {
        "collection_id": collection, "workspace_key": None, "model": _MODEL,
    }).fetchall()
    chunks = [CitedChunk(
        row["chunk_id"], row["source_id"], row["chunk_index"], row["source_name"],
        row["locator_label"], row["text"], ("keyword",), 1.0, 1, index + 1,
    ) for index, row in enumerate(chunk_rows) if row["locator_label"] == "CS 171"]
    course = load_advising_context(db, collection_id=collection, chunks=chunks)["courses"][0]
    assert course["parse_status"] == "ambiguous"
    assert course["requirements_evidence_complete"] is False
    assert course["blocker_reason"] == "source_conflict"
    assert course["prerequisites"] is None
    assert course["requisites_raw"] == "Prerequisite: MUS 221"
    assert course["citation_numbers"]
    assert course["requirement_hold"]["evidence_capture_sha256"] == capture_hash

    plan = plan_sequence({"CS 171": CourseRequirement(
        code="CS 171", title=course["title"], parse_status=course["parse_status"],
        prerequisites=course["prerequisites"], corequisites=course["corequisites"],
        requisites_raw=course["requisites_raw"],
    )}, ["CS 171"])
    assert plan.stages == ()
    assert any(blocker.reason == "requirements_unparsed" for blocker in plan.blockers)

    # Both directions skip a held node, while its own cited master text remains
    # available through the self lane for transparent explanation.
    params = {"course_id": master_course["course_id"], "collection_id": collection,
              "source_ids": None, "workspace_key": None}
    assert db.execute(queries.get("prereq_upstream"), params).fetchall() == []
    assert db.execute(queries.get("prereq_downstream"), params).fetchall() == []
    assert db.execute(queries.get("prereq_self_chunks"), {
        "collection_id": collection, "code": "CS 171", "source_ids": None,
        "workspace_key": None,
    }).fetchall()

    # The ordinary answer path has tools disabled by default. A held master
    # must not reach its prompt through keyword, vector, self, or planned hits.
    held_chunks = {chunk.chunk_id for chunk in chunks}
    for dedupe in (True, False):
        retrieval_config = lenient_config()
        retrieval_config = replace(
            retrieval_config, fusion=replace(retrieval_config.fusion, dedupe=dedupe),
        )
        retrieved = retrieve(
            db, collection_id=collection, query="CS 171 algorithms",
            embedder=_PlantedAtlasEmbedder(), embed_model=_MODEL, config=retrieval_config,
        )
        assert isinstance(retrieved, (Retrieved, Refusal))
        assert retrieved.trace.warnings
        if isinstance(retrieved, Retrieved):
            assert not held_chunks.intersection(chunk.chunk_id for chunk in retrieved.chunks)

    released = release_requirement_hold(
        db, hold_id=hold["hold_id"], source_id=source_id, course_code="CS 171",
        master_file_hash=master_hash, curator="test curator",
        release_note="Reviewed against the authoritative source.",
    )
    assert released["status"] == "released"
    assert db.execute(queries.get("prereq_upstream"), params).fetchall()


class _MissingRequirementProvider:
    def __init__(self):
        self.requests = []
        self.resource_ids = []

    def generate(self, request):
        self.requests.append(request)
        usage = Usage(40, 8, source="provider")
        call_index = len(self.requests)
        if call_index == 1:
            prompt = request.messages[1]["content"]
            self.resource_ids = list(dict.fromkeys(
                _AdvisingProvider._resource_pattern.findall(prompt)
            ))
            return GenerationResult(
                tool_calls=(ToolCall("missing-reqs", "advisor_read_course_requirements", {
                    "resource_ids": self.resource_ids, "course_codes": ["CS 301"],
                }),),
                finish_reason="tool_calls", usage=usage,
            )
        if call_index == 2:
            return GenerationResult(
                tool_calls=(ToolCall("blocked-sequence", "advisor_plan_course_sequence", {
                    "resource_ids": self.resource_ids, "selected_codes": ["CS 301"],
                    "completed_codes": [],
                }),),
                finish_reason="tool_calls", usage=usage,
            )
        if call_index == 3:
            sequence = next(
                json.loads(message.content)
                for message in request.messages
                if isinstance(message, ToolResultMessage)
                and message.name == "advisor_plan_course_sequence"
            )
            assert sequence["stages"] == []
            assert any(blocker["reason"] == "requirements_evidence_missing"
                       for blocker in sequence["blockers"])
            return GenerationResult(
                text="This excerpt does not support a safe CS 301 sequence [1].",
                finish_reason="stop", usage=usage,
            )
        raise AssertionError("query made an unexpected provider call")


def test_requirement_evidence_does_not_match_a_course_code_prefix():
    chunk = CitedChunk(
        chunk_id=uuid4(),
        source_id=uuid4(),
        chunk_index=0,
        source_name="atlas.jsonl",
        locator_label="CS 300",
        text="Prerequisite: MATH 111W",
        seams=("keyword",),
        normalized_score=1.0,
        source_slot=1,
        rank=1,
    )
    assert _requirements_are_cited("MATH 111", [chunk]) is False
    assert _requirements_are_cited("MATH 111W", [chunk]) is True


def test_query_exercises_topic_requirements_and_sequence_tools_on_imported_atlas(
    db, collection, tmp_path,
):
    source_id = _import_atlas(db, collection, tmp_path)
    before_graph = _prerequisite_snapshot(db, collection)
    provider = _AdvisingProvider()
    response = _api_client(db, provider).post("/api/query", json={
        "query": "Compare CS 110 programming foundations, CS 120 data structures, "
                 "CS 220 algorithms, and CS 230 computer systems topics and prerequisites.",
        "collectionId": collection_name(db, collection),
        "sourceIds": [str(source_id)],
    })

    if response.status_code != 200:
        ledger_rows = db.execute(
            "SELECT tool_id, status, result_json FROM tool_executions ORDER BY created_at"
        ).fetchall()
        pytest.fail(f"HTTP {response.status_code}: {response.text}; "
                    f"provider_calls={len(provider.requests)}; ledger={ledger_rows!r}")
    body = response.json()
    assert body["type"] == "answer"
    assert body["answer"].startswith("Dependency order: CS 110 then CS 120 then CS 220")
    assert len(provider.requests) == 4
    available_tools = {item.name for item in provider.requests[0].tools}
    assert {
        "advisor_explore_topics", "advisor_read_course_requirements",
        "advisor_plan_course_sequence",
    } <= available_tools
    assert provider.resource_ids
    assert set(provider.citation_by_resource) == set(provider.resource_ids)
    context_numbers = {int(number) for number, _ in provider._number_pattern.findall(
        provider.requests[0].messages[1]["content"]
    )}
    assert context_numbers == set(range(1, len(provider.resource_ids) + 1))

    tool_results = [
        json.loads(message.content)
        for message in provider.requests[-1].messages
        if isinstance(message, ToolResultMessage)
    ]
    topics, requirements, sequence = tool_results
    assert {course["code"] for group in topics["groups"]
            for course in group["supported_courses"]} == set(_COURSES)
    assert all(group["evidence_complete"] for group in topics["groups"])
    assert {course["code"] for course in requirements["courses"]} == set(_COURSES)
    assert {course["code"] for course in requirements["courses"]
            if course["parse_status"] == "parsed"} == {"CS 120", "CS 220"}
    assert sequence["stages"] == [["CS 110"], ["CS 120"], ["CS 220"]]
    assert sequence["blockers"] == [] and sequence["complete"] is True
    assert sequence["scope"] == "prerequisite_order_only"
    assert sequence["enrollment_eligibility_verified"] is False
    assert {item["code"] for item in sequence["evidence"]} == {
        "CS 110", "CS 120", "CS 220",
    }
    assert all(set(item["citation_numbers"]) <= context_numbers
               for item in sequence["evidence"])
    assert {citation["chunkId"] for citation in body["citations"]} <= set(provider.resource_ids)

    executions = db.execute(
        "SELECT tool_id, status FROM tool_executions ORDER BY tool_id"
    ).fetchall()
    assert [(row["tool_id"], row["status"]) for row in executions] == [
        ("advisor_explore_topics", "completed"),
        ("advisor_plan_course_sequence", "completed"),
        ("advisor_read_course_requirements", "completed"),
    ]
    model_calls = db.execute(
        "SELECT status, prompt_tokens, completion_tokens FROM model_calls ORDER BY created_at"
    ).fetchall()
    assert len(model_calls) == 4
    assert all(row["status"] == "completed" for row in model_calls)
    operation = db.execute("SELECT call_count, accounted_tokens FROM operations").fetchone()
    assert operation["call_count"] == 4
    assert operation["accounted_tokens"] == 440
    assert _prerequisite_snapshot(db, collection) == before_graph


def test_query_blocks_long_course_requirements_when_only_description_chunk_is_cited(
    db, collection, tmp_path,
):
    path = tmp_path / "long-course-atlas.jsonl"
    description = "observatory greenhouse lexicon " + "botany background " * 430
    path.write_text(json.dumps({
        "code": "CS 301", "title": "Computational Ecology",
        "description": description,
        "requisites": "Prerequisite: MATH 111",
    }), encoding="utf-8")
    source_id = import_atlas(
        db,
        collection_id=collection,
        path=path,
        embedder=_PlantedAtlasEmbedder(),
        embed_model=_MODEL,
        count_tokens=whitespace_tokens,
        config=load_retrieval_config(),
    )
    chunks = db.execute(
        "SELECT chunk_index, text FROM chunks WHERE source_id = %(source_id)s "
        "ORDER BY chunk_index",
        {"source_id": source_id},
    ).fetchall()
    assert len(chunks) > 1
    assert "Prerequisite: MATH 111" in chunks[-1]["text"]

    provider = _MissingRequirementProvider()
    config = lenient_config()
    config = replace(config, fusion=replace(config.fusion, cited_set_size=1))
    response = _api_client(db, provider, config=config).post("/api/query", json={
        "query": "observatory greenhouse lexicon",
        "collectionId": collection_name(db, collection),
        "sourceIds": [str(source_id)],
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "answer"
    assert "Prerequisite: MATH 111" not in body["trace"]["contextBlock"][0]["text"]
    assert len(body["trace"]["contextBlock"]) == 1
    assert len(provider.resource_ids) == 1
    tool_results = [
        json.loads(message.content)
        for message in provider.requests[-1].messages
        if isinstance(message, ToolResultMessage)
    ]
    requirements, sequence = tool_results
    requirement = requirements["courses"][0]
    assert requirement["parse_status"] == "unparsed"
    assert requirement["requirements_evidence_complete"] is False
    assert requirement["prerequisites"] is None
    assert requirement["requisites_raw"] is None
    assert requirement["blocker_reason"] == "requirements_evidence_missing"
    assert sequence["stages"] == [] and sequence["complete"] is False
    assert any(blocker["reason"] == "requirements_evidence_missing"
               for blocker in sequence["blockers"])
