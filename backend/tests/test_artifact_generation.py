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
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import run_once
from app.llm.base import GenerationResult, Usage
from tests.fakes import FakeEmbedder
from tests.helpers import collection_name, ingest_text, lenient_config, start_run


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
    inputs = {"prompt": "Alpha", "sourceIds": [str(source)]}
    run_id = start_run(client, "document", inputs, "native-once")
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
    assert start_run(client, "document", inputs, "native-once") == run_id
    assert len(provider.calls) == 1
    assert provider.calls[0].max_output_tokens == load_settings().artifact_max_output_tokens
    assert provider.calls[0].timeout_seconds == 60
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 1


def test_cancelled_model_result_is_accounted_without_publishing(db, collection, tmp_path):
    provider = SpecProvider(candidate(SPEC))
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {"prompt": "Format my supplied Alpha text.",
                                            "mode": "authoring"}, "cancelled-native")
    provider.after_call = lambda: store.cancel(UUID(run_id))
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
