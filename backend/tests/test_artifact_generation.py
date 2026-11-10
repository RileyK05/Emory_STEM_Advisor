"""The artifact pipeline checks each stage, repairs refusals, and publishes once."""
import io
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.advising.research_repository import build_scope, read_document
from app.advising.research_store import Acquired, ResearchStore
from app.api import create_app
from app.artifacts.models import ArtifactReference, DocumentSpec
from app.artifacts.service import export_artifact
from app.config import load_settings
from app.db.connection import connect, connection_dsn
from app.generation.artifact_candidate import validate_candidate
from app.generation.artifacts import build_artifact_handlers
from app.generation.native_tools import SECTION_TOOL
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import run_once
from app.llm.base import GenerationResult, TransientProviderError, Usage
from app.programs.importer import import_programs
from app.retrieval.trace import write_trace
from tests.fakes import ArtifactStageProvider, FakeEmbedder
from tests.helpers import collection_name, ingest_text, lenient_config, start_run
from tests.test_atlas_import import _SAMPLE as ATLAS_SAMPLE
from tests.test_atlas_import import _import as import_atlas_sample
from tests.test_programs import ECON_MINOR, _write_programs


def candidate(spec, bindings=()):
    return json.dumps({"spec_json": json.dumps(spec), "bindings": list(bindings)})


def message_text(message) -> str:
    return message["content"] if isinstance(message, dict) else message.content


SPEC = {"kind": "document", "title": "Guide",
        "blocks": [{"id": "p1", "type": "paragraph", "text": "Alpha facts."}]}


def outline(slots=(1,), **overrides):
    value = {
        "title": "Guide",
        "sections": [{
            "id": "s1", "heading": "Facts", "purpose": "State the facts.",
            "block_types": ["paragraph"], "source_slots": list(slots),
        }],
        "sheets": [], "charts": [],
        "program": None, "selected_courses": [], "completed_courses": [],
    }
    value.update(overrides)
    return value


def section(text="Alpha facts.", slots=(1,), **overrides):
    value = {
        "blocks": [{"id": "p1", "type": "paragraph", "text": text}],
        "bindings": [{"element_id": "p1", "source_slots": list(slots)}],
    }
    value.update(overrides)
    return value


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


def setup_runtime(db, collection, provider, tmp_path):
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake-native",
                       artifact_operation_token_budget=100_000,
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
    provider = ArtifactStageProvider(outline=outline(), sections={"s1": [section()]})
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    inputs = {"prompt": "Alpha", "sourceIds": [str(source)]}
    run_id = start_run(client, "document", inputs, "native-once")
    finished = run_once(store, "native-test", executor)
    assert finished.status.value == "succeeded", finished.error
    status = client.get(f"/api/runs/{run_id}").json()
    artifact_id = status["artifactIds"][0]
    record = client.get(f"/api/artifacts/{artifact_id}").json()
    assert record["spec"]["evidence"]["s1:p1"][0]["source_name"] == "lecture.txt"
    assert record["spec"]["blocks"][-1]["text"] == "Alpha facts."
    exported = client.get(f"/api/artifacts/{artifact_id}/download",
                          params={"revisionId": record["revisionId"], "format": "md"})
    assert exported.status_code == 200 and b"Alpha facts." in exported.content
    assert b"lecture.txt" in exported.content
    assert start_run(client, "document", inputs, "native-once") == run_id
    assert len(provider.requests) == 2
    call = provider.requests[0]
    assert call.max_output_tokens == load_settings().artifact_max_output_tokens
    assert call.timeout_seconds == 60
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 2


def test_a_draft_is_refused_then_corrected_in_the_same_conversation(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline=outline(),
        sections={"s1": [
            section(text="Alpha facts from PSYC 999.", slots=(9,)),
            section(),
        ]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {"prompt": "Alpha", "sourceIds": [str(source)]},
                       "native-corrected")
    assert run_once(store, "native-tools", executor).status.value == "succeeded"
    assert len(provider.requests) == 3
    refusal = provider.requests[2].messages[-1]
    assert "not accepted" in refusal.content and "slots" in refusal.content
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    assert client.get("/api/artifacts/" + artifact_id).json()["spec"]["blocks"][-1]["text"] \
        == "Alpha facts."


def test_a_model_that_never_corrects_fails_cleanly_with_usage_accounted(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline=outline(), sections={"s1": [section(text="Bad PSYC 999.", slots=(9,))]})
    _, store, executor = setup_runtime(db, collection, provider, tmp_path)
    store.create(kind="artifact.run", scope_key="development", idempotency_key="never-corrects",
                 payload={"kind": "document", "inputs": {
                     "prompt": "Alpha", "sourceIds": [str(source)]}}, max_attempts=1)
    finished = run_once(store, "native-tools", executor)
    assert finished.status.value == "failed"
    assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0
    calls = db.execute("SELECT prompt_tokens, completion_tokens FROM model_calls").fetchall()
    assert len(calls) == 1 + 2 + 1  # outline + one refusal + repeated refusal
    assert all(call["prompt_tokens"] == 100 and call["completion_tokens"] == 30 for call in calls)
    operation = db.execute("SELECT accounted_tokens FROM operations").fetchone()
    assert operation["accounted_tokens"] == 130 * len(calls)


def test_cancelled_model_result_is_accounted_without_publishing(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")

    class Cancelling(ArtifactStageProvider):
        def generate(self, request):
            result = super().generate(request)
            store.cancel(UUID(run_id))
            return result

    provider = Cancelling(outline=outline(), sections={"s1": [section()]})
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {"prompt": "Alpha", "sourceIds": [str(source)]},
                       "cancelled-native")
    finished = run_once(store, "native-cancel", executor)
    assert finished.status.value == "cancelled"
    assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0
    call = db.execute("SELECT prompt_tokens, completion_tokens FROM model_calls").fetchone()
    assert call["prompt_tokens"] == 100 and call["completion_tokens"] == 30


def test_empty_grounded_source_scope_never_calls_native_model(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(outline=outline(), sections={"s1": [section()]})
    _, store, executor = setup_runtime(db, collection, provider, tmp_path)
    store.create(kind="artifact.run", scope_key="development", idempotency_key="empty",
                 payload={"kind": "document", "inputs": {"prompt": "Alpha", "sourceIds": []}},
                 max_attempts=1)
    finished = run_once(store, "native-empty", executor)
    assert finished.status.value == "failed", finished.error
    assert finished.error["type"] == "no_grounding" and provider.requests == []
    assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0


def test_research_grounding_uses_the_runs_evidence_as_the_numbered_slots(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    scope = build_scope(db, collection_id=collection, workspace_key="development",
                        source_ids=[source])
    trace_id = write_trace(db, collection_id=collection, query="Alpha", config_version=1,
                           embed_model=None, refused=False, seams=[], contributions=[],
                           workspace_key="development")
    db.commit()
    store = ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="test", config_version="1",
                         root_trace_id=trace_id)
    page = read_document(db, scope, source)
    store.acquire(run.run_id, [Acquired(page.chunks[0], scope.sources[source])],
                  action_kind="read", summary="read the note")
    db.commit()
    evidence_chunk = str(page.chunks[0].chunk_id)

    provider = ArtifactStageProvider(outline=outline(), sections={"s1": [section()]})
    client, run_store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {
        "prompt": "Alpha", "sourceIds": [str(source)],
        "researchRunId": str(run.run_id),
    }, "research-grounded")
    assert run_once(run_store, "research-slots", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    record = client.get("/api/artifacts/" + artifact_id).json()
    assert record["spec"]["evidence"]["s1:p1"][0]["chunk_id"] == evidence_chunk


def test_workbook_rows_become_code_built_cells_and_formulas(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline={
            "title": "Credits", "sections": [],
            "sheets": [{
                "id": "s1", "name": "Credits", "row_purpose": "One course per row.",
                "columns": [
                    {"key": "course", "header": "Course", "type": "text"},
                    {"key": "credits", "header": "Credits", "type": "number"},
                    {"key": "total", "header": "Total", "type": "total"},
                ],
                "source_slots": [1],
            }],
            "charts": [{
                "sheet_id": "s1", "type": "bar", "title": "Credits",
                "value_column": "credits", "category_column": "course",
            }],
        },
        sections={"s1": [{
            "rows": [{"values": ["Alpha 101", 4, None], "source_slots": [1]}],
        }]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "workbook", {"prompt": "Alpha facts", "sourceIds": [str(source)]},
                       "workbook-rows")
    finished = run_once(store, "workbook", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get("/api/artifacts/" + artifact_id).json()["spec"]
    sheet = spec["sheets"][0]
    cells = {(cell["row"], cell["column"]): cell for cell in sheet["cells"]}
    assert cells[(1, 1)]["value"] == "Course" and cells[(2, 2)]["value"] == 4
    assert cells[(2, 3)]["formula"] == "=SUM(B2:B2)"
    assert spec["charts"][0]["data_range"] == "B1:B2"
    assert spec["charts"][0]["categories_range"] == "A1:A2"
    exported = client.get(f"/api/artifacts/{artifact_id}/download",
                          params={"revisionId": client.get("/api/artifacts/" + artifact_id)
                                  .json()["revisionId"], "format": "xlsx"})
    assert exported.status_code == 200
    import io

    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=False)
    assert workbook["Credits"]["C2"].value == "=SUM(B2:B2)"


def _plan_outline(selected, completed=(), program="Economics (Minor)"):
    return {
        "title": "Plan", "sections": [{
            "id": "draft", "heading": "Draft", "purpose": "Draft.",
            "block_types": ["paragraph"], "source_slots": [],
        }],
        "sheets": [], "charts": [], "program": program,
        "selected_courses": list(selected), "completed_courses": list(completed),
    }


def _plan_section(text):
    def build(payload):
        slots = payload["section"]["source_slots"]
        return {
            "blocks": [{"id": "p1", "type": "paragraph", "text": text}],
            "bindings": [{"element_id": "p1", "source_slots": [slots[0]]}],
            "courses": payload["section"]["courses"],
        }

    return build


def _econ(db, collection, tmp_path, extra="", program=ECON_MINOR):
    source = import_atlas_sample(db, collection, tmp_path, text=ATLAS_SAMPLE + extra)
    path = _write_programs(tmp_path, ("Economics (Minor)", program))
    import_programs(db, collection_id=collection, path=path)
    db.commit()
    return source


def test_a_plan_is_ordered_by_the_parser_and_not_by_the_model(db, collection, tmp_path):
    source = _econ(db, collection, tmp_path)
    provider = ArtifactStageProvider(
        outline=_plan_outline(["ECON 201"], completed=["ECON 101"]),
        sections={"term1": [_plan_section(
            "ECON 201 Intermediate Macroeconomics, MW 11:00-12:15.")]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Plan ECON 201 with ECON 101 done",
                        "sourceIds": [str(source)]}, "plan-ordered")
    finished = run_once(store, "plan", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"]
                if block.get("type") == "heading"]
    assert "Term 1" in headings
    assert "degraded_sections" not in spec.get("provenance", {})
    assert any("ECON 201" in block.get("text", "") for block in spec["blocks"])


def test_a_section_that_moves_a_course_between_terms_is_refused(db, collection, tmp_path):
    source = _econ(db, collection, tmp_path)
    wrong = _plan_section("MATH 111 is scheduled here.")
    correct = _plan_section("MATH 112 Calculus II.")
    provider = ArtifactStageProvider(
        outline=_plan_outline(["MATH 111", "MATH 112"]),
        sections={"term1": [_plan_section("MATH 111 Calculus I.")],
                  "term2": [wrong, correct]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Plan MATH 111 then MATH 112",
                        "sourceIds": [str(source)]}, "plan-moved")
    finished = run_once(store, "plan", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    text = json.dumps(spec)
    assert "Term 1" in text and "Term 2" in text
    # Sections run in parallel, so term 2's correction need not be the last request.
    assert any("MATH 112" in message_text(request.messages[-1])
               and "not accepted" in message_text(request.messages[-1])
               for request in provider.requests)


def test_a_failed_plan_section_falls_back_to_the_fixed_course_list(db, collection, tmp_path):
    source = _econ(db, collection, tmp_path)

    class FailingSections(ArtifactStageProvider):
        def generate(self, request):
            if request.tools[0].name == SECTION_TOOL:
                return GenerationResult(text="", finish_reason="length",
                                        usage=Usage(100, 30, source="provider"))
            return super().generate(request)

    provider = FailingSections(outline=_plan_outline(["MATH 111", "MATH 112"]))
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Plan MATH 111 then MATH 112",
                        "sourceIds": [str(source)]}, "plan-fallback")
    finished = run_once(store, "plan", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"]
                if block.get("type") == "heading"]
    assert "Term 1" in headings and "Term 2" in headings
    assert spec["provenance"]["degraded_sections"] == 2
    assert any("MATH 111" in block.get("text", "") for block in spec["blocks"])
    assert any("MATH 112" in block.get("text", "") for block in spec["blocks"])
    # Each fallback course list cites the records of its own courses.
    lists = [block for block in spec["blocks"] if block["id"].endswith("fallback-courses")]
    assert lists
    for block in lists:
        codes = [item.split(" ")[0] + " " + item.split(" ")[1] for item in block["items"]]
        for ref in spec["evidence"][block["id"]]:
            assert any(code in ref["text_snapshot"] for code in codes), ref["locator_label"]


def test_a_truncated_section_is_reprompted_and_published(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    truncated = GenerationResult(
        text="I worked through the section but stopped at the limit.",
        finish_reason="length",
        usage=Usage(100, 8_192, source="provider"),
    )
    provider = ArtifactStageProvider(
        outline=outline(),
        sections={"s1": [truncated, section()]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Alpha", "sourceIds": [str(source)]}, "truncated-section")
    finished = run_once(store, "truncated-section", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    assert any("Alpha facts." in block.get("text", "") for block in spec["blocks"])
    assert "output token limit" in message_text(provider.requests[-1].messages[-1])
    assert "degraded_sections" not in spec.get("provenance", {})


def test_a_transient_provider_failure_is_retried(db, collection, tmp_path, monkeypatch):
    from tests.fakes import FakeClock

    clock = FakeClock()
    monkeypatch.setattr("app.generation.controller._sleep", clock.sleep)
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")

    class FlakyOnce(ArtifactStageProvider):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.failed = False

        def generate(self, request):
            if (not self.failed and request.tools
                    and request.tools[0].name == SECTION_TOOL):
                self.failed = True
                self.requests.append(request)
                raise TransientProviderError("connection dropped", provider="fake")
            return super().generate(request)

    provider = FlakyOnce(outline=outline(), sections={"s1": [section()]})
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Alpha", "sourceIds": [str(source)]}, "flaky-section")
    finished = run_once(store, "flaky-section", executor)
    assert finished.status.value == "succeeded", finished.error
    assert clock.slept == [1.0], "one backoff wait inside the operation deadline"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    assert any("Alpha facts." in block.get("text", "") for block in spec["blocks"])


def test_a_blocked_course_goes_in_the_advisor_section(db, collection, tmp_path):
    source = _econ(
        db, collection, tmp_path,
        extra='\n{"code": "ECON 499", "title": "Independent Study", '
              '"description": "Permission only.", "requisites": "Instructor permission required."}',
        program="Economics (Minor)\nDEGREE AWARDED N/A\nRequirements\n• Econ 499\n",
    )
    provider = ArtifactStageProvider(
        outline=_plan_outline(["ECON 499"]),
        sections={"advisor": [_plan_section("ECON 499 needs instructor permission.")]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Plan ECON 499 for the Economics minor",
                        "sourceIds": [str(source)]}, "plan-blocked")
    finished = run_once(store, "plan", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"]
                if block.get("type") == "heading"]
    assert "Check with your advisor" in headings
    assert any("ECON 499" in block.get("text", "") for block in spec["blocks"])


def test_a_revision_replaces_one_section_and_keeps_the_rest_byte_identical(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    create_provider = ArtifactStageProvider(
        outline={
            "title": "Guide", "sections": [
                {"id": "s1", "heading": "First", "purpose": "First.",
                 "block_types": ["paragraph"], "source_slots": [1]},
                {"id": "s2", "heading": "Second", "purpose": "Second.",
                 "block_types": ["paragraph"], "source_slots": [1]},
            ],
            "sheets": [], "charts": [],
            "program": None, "selected_courses": [], "completed_courses": [],
        },
        sections={
            "s1": [section(text="Alpha facts.")],
            "s2": [section(text="Alpha facts too.")],
        },
    )
    client, store, executor = setup_runtime(db, collection, create_provider, tmp_path)
    run_id = start_run(client, "document", {"prompt": "Alpha", "sourceIds": [str(source)]},
                       "revision-base")
    assert run_once(store, "revision-base", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    prior = client.get(f"/api/artifacts/{artifact_id}").json()
    prior_s1 = [block for block in prior["spec"]["blocks"]
                if block["id"].startswith("s1:")]
    prior_s1_evidence = {key: value for key, value in prior["spec"]["evidence"].items()
                         if key.startswith("s1:")}

    revise_provider = ArtifactStageProvider(
        edits=[{
            "title": "Guide",
            "operations": [
                {"action": "keep", "section_id": "s1"},
                {"action": "replace", "section_id": "s2",
                 "section": {"id": "s2", "heading": "Changed", "purpose": "Changed.",
                             "block_types": ["paragraph"], "source_slots": [1]}},
            ],
        }],
        sections={"s2": [section(text="Alpha facts revised.")]},
    )
    revise_client, revise_store, revise_executor = setup_runtime(
        db, collection, revise_provider, tmp_path)
    start_run(revise_client, "document", {
        "prompt": "Alpha facts revision", "sourceIds": [str(source)],
        "artifactId": artifact_id, "expectedRevisionId": prior["revisionId"],
    }, "revision-one-section")
    finished = run_once(revise_store, "revision", revise_executor)
    assert finished.status.value == "succeeded", finished.error
    revised = revise_client.get(f"/api/artifacts/{artifact_id}").json()
    revised_s1 = [block for block in revised["spec"]["blocks"]
                  if block["id"].startswith("s1:")]
    revised_s1_evidence = {key: value for key, value in revised["spec"]["evidence"].items()
                           if key.startswith("s1:")}
    assert revised_s1 == prior_s1
    assert revised_s1_evidence == prior_s1_evidence
    changed = [block for block in revised["spec"]["blocks"] if block["id"].startswith("s2:")]
    assert any(block.get("text") == "Alpha facts revised." for block in changed)
    assert any(block.get("text") == "Changed" for block in changed)
    assert revised["parentRevisionId"] == prior["revisionId"]
