"""Native model output runs through validation, accounting, and fenced persistence."""
import io
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.api import create_app
from app.artifacts.models import ArtifactReference, DocumentSpec
from app.artifacts.service import export_artifact
from app.config import load_settings
from app.db.connection import connect, connection_dsn
from app.generation.artifacts import build_artifact_handlers, validate_candidate
from app.ingest.pipeline import ingest_file
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import run_once
from app.llm.base import GenerationResult, Usage
from tests.fakes import FakeEmbedder, whitespace_tokens
from tests.helpers import collection_name, ingest_text, lenient_config


def candidate(spec, bindings=()):
    return json.dumps({"spec_json": json.dumps(spec), "bindings": list(bindings)})


SPEC = {"kind": "document", "title": "Guide",
        "blocks": [{"id": "p1", "type": "paragraph", "text": "Alpha facts."}]}


def test_grounded_candidate_requires_every_element_and_valid_supplied_slot():
    chunk = SimpleNamespace(chunk_id=uuid4(), source_id=uuid4(), source_name="lecture.txt",
                            locator_label="lines 1-3", text="Alpha facts.")
    with pytest.raises(ValueError, match="every content"):
        validate_candidate("document", candidate(SPEC), [chunk], mode="grounded")
    with pytest.raises(ValueError, match="outside"):
        validate_candidate("document", candidate(SPEC, [{"element_id": "p1", "source_slots": [2]}]),
                           [chunk], mode="grounded")
    valid = validate_candidate("document", candidate(SPEC, [
        {"element_id": "p1", "source_slots": [1]}]), [chunk], mode="grounded")
    assert valid.evidence["p1"][0].text_snapshot == "Alpha facts."
    assert valid.evidence["p1"][0].chunk_id == chunk.chunk_id
    assert valid.provenance["authority"] == "generated_draft"


def test_authoring_rejects_fabricated_source_bindings_and_overwrites_model_provenance():
    with pytest.raises(ValueError, match="invent"):
        validate_candidate("document", candidate(SPEC, [
            {"element_id": "p1", "source_slots": [1]}]), [], mode="authoring")
    spec = validate_candidate("document", candidate({
        **SPEC, "provenance": {"authority": "verified_institutional_fact"}}), [], mode="authoring")
    assert spec.provenance["authority"] == "generated_draft"


def test_snapshot_source_legends_survive_markdown_word_html_exports():
    ref = ArtifactReference(chunk_id=uuid4(), source_id=uuid4(), source_name="=Source <file>.pdf",
                            locator_label="page 7", text_snapshot="Alpha facts.")
    spec = DocumentSpec.model_validate({**SPEC, "evidence": {"p1": [ref.model_dump(mode="json")]}})
    md = export_artifact(spec, "md").content.decode()
    assert "page 7" in md and str(ref.chunk_id) in md
    html = export_artifact(spec, "html").content.decode()
    assert "&lt;file&gt;" in html
    doc = Document(io.BytesIO(export_artifact(spec, "docx").content))
    assert any("page 7" in p.text and str(ref.chunk_id) in p.text for p in doc.paragraphs)


class SpecProvider:
    def __init__(self, text, after_call=None):
        self.text, self.calls, self.after_call = text, [], after_call

    def generate(self, request):
        self.calls.append(request)
        if self.after_call:
            self.after_call()
        return GenerationResult(text=self.text, finish_reason="stop",
                                usage=Usage(100, 30, source="provider"), model="fake-native")


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def generate(self, request):
        self.calls.append(request)
        text = self.outputs.pop(0)
        return GenerationResult(text=text, finish_reason="stop",
                                usage=Usage(100, 30, source="provider"), model="fake-native")


def setup_runtime(db, collection, provider, tmp_path):
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake-native",
                       operation_token_budget=100_000,
                       default_collection=collection_name(db, collection), data_dir=tmp_path,
                       database_url=connection_dsn(db))
    config = lenient_config()
    store = JobStore(lambda: connect(connection_dsn(db)))
    handlers = build_artifact_handlers(settings, config, FakeEmbedder(8), provider,
                                      lambda: connect(connection_dsn(db)))
    app = create_app(allow_test_client=True, settings=settings, config=config, embedder=FakeEmbedder(8), provider=provider)
    app.state.override_conn, app.state.job_store = db, store
    return TestClient(app), store, RunExecutor(handlers, data_dir=tmp_path)


def test_http_job_worker_artifact_revision_and_download_end_to_end(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = SpecProvider(candidate(SPEC, [{"element_id": "p1", "source_slots": [1]}]))
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    body = {"kind": "document", "inputs": {"prompt": "Alpha", "sourceIds": [str(source)]},
            "idempotencyKey": "native-once"}
    queued = client.post("/api/runs", json=body)
    assert queued.status_code == 200
    run_id = queued.json()["runId"]
    finished = run_once(store, "native-test", executor)
    assert finished.status.value == "succeeded", finished.error
    status = client.get(f"/api/runs/{run_id}").json()
    artifact_id = status["artifactIds"][0]
    record = client.get(f"/api/artifacts/{artifact_id}").json()
    assert record["spec"]["evidence"]["p1"][0]["source_name"] == "lecture.txt"
    exported = client.get(f"/api/artifacts/{artifact_id}/download",
                          params={"revisionId": record["revisionId"], "format": "md"})
    assert exported.status_code == 200 and b"Alpha facts." in exported.content
    assert b"lecture.txt" in exported.content
    assert client.post("/api/runs", json=body).json()["runId"] == run_id
    assert len(provider.calls) == 1
    assert provider.calls[0].max_output_tokens == 1024
    assert provider.calls[0].timeout_seconds == 60
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 1


def test_cancelled_model_result_is_accounted_without_publishing(db, collection, tmp_path):
    provider = SpecProvider(candidate(SPEC))
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    created = client.post("/api/runs", json={
        "kind": "document", "inputs": {"prompt": "Format my supplied Alpha text.", "mode": "authoring"},
        "idempotencyKey": "cancelled-native"}).json()
    provider.after_call = lambda: store.cancel(UUID(created["runId"]))
    finished = run_once(store, "native-cancel", executor)
    assert finished.status.value == "cancelled"
    assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0
    call = db.execute("SELECT prompt_tokens, completion_tokens FROM model_calls").fetchone()
    assert call["prompt_tokens"] == 100 and call["completion_tokens"] == 30


def test_empty_grounded_source_scope_never_calls_native_model(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = SpecProvider(candidate(SPEC))
    _, store, executor = setup_runtime(db, collection, provider, tmp_path)
    store.create(kind="artifact.run", scope_key="development", idempotency_key="empty",
                 payload={"kind": "document", "inputs": {"prompt": "Alpha", "sourceIds": []}},
                 max_attempts=1)
    finished = run_once(store, "native-empty", executor)
    assert finished.status.value == "failed", finished.error
    assert finished.error["type"] == "no_grounding" and provider.calls == []
    assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0


def _planned_document_body(key):
    return {"kind": "document", "inputs": {
        "prompt": "Write three words about the requested topic.", "mode": "authoring",
        "outputPlan": {"kind": "document", "title": "Planned guide", "sections": [{
            "key": key, "title": "Opening", "min_words": 3, "target_words": 3,
            "max_words": 3, "min_blocks": 1, "target_blocks": 1, "max_blocks": 1,
        }], "max_total_blocks": 2, "max_output_tokens_per_unit": 64,
            "max_repairs_per_unit": 1},
    }}


def test_dependent_document_plan_assembles_only_validated_sections(db, collection, tmp_path):
    output = json.dumps({"blocks": [{"id": "intro.paragraph", "type": "paragraph",
                                     "text": "Alpha beta gamma."}], "evidence": {}})
    provider = SpecProvider(output)
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    created = client.post("/api/runs", json={**_planned_document_body("intro"),
                                              "idempotencyKey": "planned-document"})
    assert created.status_code == 200, created.text
    finished = run_once(store, "planned-document-worker", executor)
    assert finished.status.value == "succeeded", finished.error
    assert len(provider.calls) == 1
    public = client.get(f"/api/runs/{created.json()['runId']}").json()
    assert public["artifactRevisions"] == [{
        "artifactId": public["artifactIds"][0],
        "revisionId": public["artifactRevisions"][0]["revisionId"],
    }]
    events = client.get(f"/api/runs/{created.json()['runId']}/events").json()
    run_events = store.events(UUID(created.json()["runId"]))
    terminal_event_id = next(event.event_id for event in run_events
                             if event.event_type == "succeeded")
    succeeded = next(event for event in events if event["eventId"] == terminal_event_id)
    assert succeeded["artifactRevisions"] == public["artifactRevisions"]
    artifact = client.get(f"/api/artifacts/{public['artifactIds'][0]}").json()
    blocks = artifact["spec"]["blocks"]
    assert [block["id"] for block in blocks] == ["intro.heading", "intro.paragraph"]
    assert db.execute("SELECT status FROM artifact_run_plans").fetchone()["status"] == "validated"
    pinned = public["artifactRevisions"][0]["revisionId"]
    advanced = client.post(f"/api/artifacts/{public['artifactIds'][0]}/revisions", json={
        "expectedParentRevisionId": pinned, "draftId": "later-manual-edit",
        "spec": {**artifact["spec"], "title": "Later edit"},
    })
    assert advanced.status_code == 200, advanced.text
    historical = client.get(
        f"/api/artifacts/{public['artifactIds'][0]}", params={"revisionId": pinned}
    ).json()
    assert historical["revisionId"] == pinned
    assert historical["spec"]["title"] == "Planned guide"


def test_dependent_document_repairs_only_known_invalid_section_output(db, collection, tmp_path):
    invalid = json.dumps({"blocks": [{"id": "intro.paragraph", "type": "paragraph",
                                      "text": "Too few."}], "evidence": {}})
    valid = json.dumps({"blocks": [{"id": "intro.paragraph", "type": "paragraph",
                                    "text": "Alpha beta gamma."}], "evidence": {}})
    provider = SequenceProvider([invalid, valid])
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    created = client.post("/api/runs", json={**_planned_document_body("intro"),
                                              "idempotencyKey": "planned-repair"})
    assert created.status_code == 200, created.text
    finished = run_once(store, "planned-repair-worker", executor)
    assert finished.status.value == "succeeded"
    assert len(provider.calls) == 2
    job_id = UUID(created.json()["runId"])
    attempts = db.execute(
        "SELECT outcome,candidate_text,validation_issues FROM artifact_run_unit_attempts "
        "WHERE job_id=%s ORDER BY attempt_no", (job_id,),
    ).fetchall()
    assert len(attempts) == 1 and attempts[0]["outcome"] == "invalid"
    assert attempts[0]["candidate_text"] == invalid
    assert attempts[0]["validation_issues"]


def test_dependent_document_revision_appends_units_and_preserves_prior_content(
    db, collection, tmp_path
):
    provider = SpecProvider(json.dumps({
        "blocks": [{"id": "next.paragraph", "type": "paragraph",
                    "text": "Alpha beta gamma."}], "evidence": {},
    }))
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    prior = client.post("/api/artifacts", json={"kind": "document", "spec": {
        "kind": "document", "title": "Existing", "blocks": [
            {"id": "prior.paragraph", "type": "paragraph", "text": "Previously authored."},
        ],
    }}).json()
    body = _planned_document_body("next")
    body["inputs"].update({"artifactId": prior["artifactId"],
                           "expectedRevisionId": prior["revisionId"]})
    body["inputs"]["outputPlan"]["max_total_blocks"] = 3
    queued = client.post("/api/runs", json={**body, "idempotencyKey": "planned-revision"})
    assert queued.status_code == 200, queued.text
    finished = run_once(store, "planned-revision-worker", executor)
    assert finished.status.value == "succeeded", finished.error
    updated = client.get(f"/api/artifacts/{prior['artifactId']}").json()
    assert [block["id"] for block in updated["spec"]["blocks"]] == [
        "prior.paragraph", "next.heading", "next.paragraph",
    ]


def test_stale_expected_parent_rejects_plan_before_provider_call(db, collection, tmp_path):
    provider = SequenceProvider([])
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    prior_spec = {"kind": "document", "title": "Existing", "blocks": [
        {"id": "prior.paragraph", "type": "paragraph", "text": "Previously authored."},
    ]}
    prior = client.post("/api/artifacts", json={"kind": "document", "spec": prior_spec}).json()
    advanced = {**prior_spec, "blocks": [
        {"id": "prior.paragraph", "type": "paragraph", "text": "Changed elsewhere."},
    ]}
    response = client.post(f"/api/artifacts/{prior['artifactId']}/revisions", json={
        "expectedParentRevisionId": prior["revisionId"], "draftId": "external-change",
        "spec": advanced,
    })
    assert response.status_code == 200
    body = _planned_document_body("next")
    body["inputs"].update({"artifactId": prior["artifactId"],
                           "expectedRevisionId": prior["revisionId"]})
    body["inputs"]["outputPlan"]["max_total_blocks"] = 3
    queued = client.post("/api/runs", json={**body, "idempotencyKey": "stale-parent"})
    assert queued.status_code == 200
    finished = run_once(store, "stale-parent-worker", executor)
    assert finished.error["type"] == "stale_plan"
    assert provider.calls == []
    assert db.execute("SELECT count(*) AS n FROM artifact_run_plans").fetchone()["n"] == 0


def test_dependent_workbook_uses_declared_columns_and_exact_cell_quota(db, collection, tmp_path):
    output = json.dumps({"rows": [["Alpha", 1], ["Beta", 2]], "formulas": {}, "evidence": {}})
    provider = SpecProvider(output)
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    body = {"kind": "workbook", "inputs": {
        "prompt": "Create two rows of supplied topic labels and sequence numbers.",
        "mode": "authoring", "outputPlan": {
            "kind": "workbook", "title": "Planned data", "sheets": [{
                "key": "data", "name": "Data", "columns": [
                    {"key": "topic", "label": "Topic", "value_type": "text"},
                    {"key": "sequence", "label": "Sequence", "value_type": "integer"},
                ], "min_rows": 2, "target_rows": 2, "max_rows": 2,
            }], "max_total_cells": 6, "max_output_tokens_per_unit": 64,
            "max_repairs_per_unit": 1,
        },
    }, "idempotencyKey": "planned-workbook"}
    created = client.post("/api/runs", json=body)
    assert created.status_code == 200, created.text
    finished = run_once(store, "planned-workbook-worker", executor)
    assert finished.status.value == "succeeded", finished.error
    assert len(provider.calls) == 1
    public = client.get(f"/api/runs/{created.json()['runId']}").json()
    record = client.get(f"/api/artifacts/{public['artifactIds'][0]}").json()
    cells = record["spec"]["sheets"][0]["cells"]
    assert len(cells) == 6
    assert [(cell["row"], cell["column"], cell["value"]) for cell in cells] == [
        (1, 1, "Topic"), (1, 2, "Sequence"), (2, 1, "Alpha"),
        (2, 2, 1), (3, 1, "Beta"), (3, 2, 2),
    ]


def test_source_reindex_after_staging_fences_final_publication(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha beta gamma.")
    output = json.dumps({
        "blocks": [{"id": "intro.paragraph", "type": "paragraph", "text": "Alpha beta gamma."}],
        "evidence": {"intro.paragraph": [1]},
    })
    provider = SpecProvider(output)
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    body = _planned_document_body("intro")
    body["inputs"]["prompt"] = "Alpha beta gamma"
    body["inputs"].update({"mode": "grounded", "sourceIds": [str(source_id)]})
    created = client.post("/api/runs", json={**body, "idempotencyKey": "reindex-before-publish"})
    assert created.status_code == 200, created.text
    source = db.execute("SELECT index_revision FROM sources WHERE source_id=%s", (source_id,)).fetchone()

    class ReindexBeforePublish(RunExecutor):
        def __call__(self, job, context):
            staged = super().__call__(job, context)
            conn = connect(connection_dsn(db))
            try:
                ingest_file(
                    conn, collection_id=collection, path=tmp_path / "lecture.txt",
                    content_type="text/plain", count_tokens=whitespace_tokens,
                    embedder=FakeEmbedder(8), embed_model="test-embed", config=lenient_config(),
                    workspace_key=None, force=True,
                    expected_index_revision=source["index_revision"],
                )
                conn.commit()
            finally:
                conn.close()
            return staged

    guarded = ReindexBeforePublish(executor._handlers, data_dir=tmp_path)
    finished = run_once(store, "reindex-before-publish-worker", guarded)
    assert finished.status.value == "failed", finished.error
    assert finished.error["type"] == "stale_plan"
    assert provider.calls
    assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 1
