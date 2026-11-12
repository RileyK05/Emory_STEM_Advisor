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
from app.generation.native_tools import OUTLINE_TOOL, SECTION_TOOL
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
    assert "page 7" in md
    html = export_artifact(spec, "html").content.decode()
    assert "&lt;file&gt;" in html
    doc = Document(io.BytesIO(export_artifact(spec, "docx").content))
    assert any("page 7" in p.text for p in doc.paragraphs)
    # A reader sees where a fact came from, never the ids that bind it.
    word = "\n".join(p.text for p in doc.paragraphs)
    for exported in (md, html, word):
        assert str(ref.chunk_id) not in exported and str(ref.source_id) not in exported
        assert "p1:" not in exported


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


def test_an_authoring_page_publishes_without_corpus_evidence(db, collection, tmp_path):
    """A page built from the student's own text carries no citations by design."""
    provider = ArtifactStageProvider(
        outline={"title": "Safe page", "sections": [{
            "id": "s1", "heading": "Page", "purpose": "One page.",
            "block_types": ["paragraph"]}], "sheets": [], "charts": []},
        sections={"s1": [{"html": "<p>Hello</p>", "blocks": [], "rows": []}]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "html", {
        "prompt": "Make a page with <script>alert(1)</script>",
        "mode": "authoring",
    }, "authoring-page")
    assert run_once(store, "authoring-page", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    assert spec["provenance"]["generation_mode"] == "authoring"
    assert spec["evidence"] == {}


@pytest.mark.parametrize(("kind", "text", "template"), [
    ("document", "Make a checklist of everything the NBB BS requires.", "checklist"),
    ("document", "Compare the Computer Science BA and BS.", "comparison"),
    ("document", "Give me a guide to the Physics minor.", "report"),
    ("workbook", "Spreadsheet comparing the Mathematics BA and BS.", "comparison_sheet"),
    ("workbook", "Spreadsheet of the CHEM prerequisite chain.", "prerequisite"),
    ("workbook", "Track my Biology BS credits with totals.", "tracker"),
    ("html", "Make a one-page web page roadmap.", "page"),
    ("document", "Write me an essay about guilt.", None),
])
def test_common_artifacts_get_a_base_template(kind, text, template):
    from app.generation.artifact_templates import choose_template

    chosen = choose_template(kind, text)
    assert (chosen.id if chosen else None) == template


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


_TABLE = {"id": "t1", "type": "table", "headers": ["Course", "Credits"]}


@pytest.mark.parametrize(("kind", "block_types", "drafts", "requests", "published"), [
    # Unused fields arrive empty, and a block cites its own slots.
    ("document", ["paragraph"], [{"blocks": [{"id": "p1", "type": "paragraph",
                                               "text": "Alpha facts.", "source_slots": [1]}],
                                  "html": None, "rows": []}], 2, "Alpha facts."),
    # A slot number written as text is the same slot.
    ("document", ["paragraph"], [{"blocks": [{"id": "p1", "type": "paragraph",
                                               "text": "Alpha facts.", "source_slots": ["1"]}]}],
     2, "Alpha facts."),
    # One slot sent without its list is the same slot.
    ("document", ["paragraph"], [{"blocks": [{"id": "p1", "type": "paragraph",
                                               "text": "Alpha facts.", "source_slots": 1}]}],
     2, "Alpha facts."),
    # A year written as a number is a header's text.
    ("document", ["table"], [{"blocks": [{**_TABLE, "headers": ["Course", 2026],
                                           "rows": [["Alpha 101", "4"]], "source_slots": [1]}]}],
     2, "2026"),
    # Credits written as a number are text in a table, and a null cell is empty.
    ("document", ["table"], [{"blocks": [{**_TABLE, "rows": [["Alpha 101", 4]],
                                           "source_slots": [1]}]}], 2, "Alpha 101"),
    ("document", ["table"], [{"blocks": [{**_TABLE, "rows": [["Alpha 101", 4], ["Alpha 102", None]],
                                           "source_slots": [1]}]}], 2, "Alpha 102"),
    ("document", ["table"], [{"blocks": [{**_TABLE, "rows": [["Alpha 101", True]],
                                           "source_slots": [1]}]},
                             {"blocks": [{**_TABLE, "rows": [["Alpha 101", "4"]],
                                          "source_slots": [1]}]}], 3, "Alpha 101"),
    # A block cannot cite a slot outside its section.
    ("document", ["paragraph"], [{"blocks": [{"id": "p1", "type": "paragraph", "text": "Alpha.",
                                               "source_slots": [9]}]}, section()], 3, "Alpha facts."),
    # A page names its slots; code builds the one binding.
    ("html", ["paragraph"], [{"html": "<p>Alpha facts.</p>", "source_slots": [1],
                              "blocks": [], "rows": None}], 2, "Alpha facts."),
    ("html", ["paragraph"], [{"html": "<p>Alpha facts.</p>"},
                             {"html": "<p>Alpha facts.</p>", "source_slots": [1]}], 3,
     "Alpha facts."),
])
def test_a_section_is_accepted_in_the_shape_its_kind_offers(
        db, collection, tmp_path, kind, block_types, drafts, requests, published):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline=outline(sections=[{"id": "s1", "heading": "Facts", "purpose": "State the facts.",
                                   "block_types": block_types, "source_slots": [1]}]),
        sections={"s1": drafts})
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, kind, {"prompt": "Alpha", "sourceIds": [str(source)]},
                       f"section-shape-{kind}-{requests}-{len(drafts)}-{block_types[0]}")
    finished = run_once(store, "section-shape", executor)
    assert finished.status.value == "succeeded", finished.error
    assert len(provider.requests) == requests
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get("/api/artifacts/" + artifact_id).json()["spec"]
    assert published in json.dumps(spec)
    if "Credits" in json.dumps(drafts):
        table = next(block for block in spec["blocks"] if block["type"] == "table")
        assert ["Alpha 101", "4"] in table["rows"]


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
    assert finished.error["type"] == "section_exhausted"
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


@pytest.mark.parametrize("stored", [True, False])
def test_research_grounding_uses_the_runs_evidence_as_the_numbered_slots(
        db, collection, tmp_path, stored):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    atlas = import_atlas_sample(db, collection, tmp_path)
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
    if stored:
        store.acquire(run.run_id, [Acquired(page.chunks[0], scope.sources[source])],
                      action_kind="read", summary="read the note")
    db.commit()

    provider = ArtifactStageProvider(outline=outline(), sections={"s1": [section()]})
    client, run_store, executor = setup_runtime(db, collection, provider, tmp_path)
    # A run that stored no passage is still grounded by the course record it names.
    run_id = start_run(client, "document", {
        "prompt": "Alpha" if stored else "A checklist for ECON 201",
        "sourceIds": [str(source)] if stored else [str(source), str(atlas)],
        "researchRunId": str(run.run_id),
    }, "research-grounded")
    assert run_once(run_store, "research-slots", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    record = client.get("/api/artifacts/" + artifact_id).json()
    cited = record["spec"]["evidence"]["s1:p1"][0]
    assert (cited["chunk_id"] == str(page.chunks[0].chunk_id)) is stored
    assert stored or cited["source_id"] == str(atlas)


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


@pytest.mark.parametrize(("brief", "title", "refused"), [
    ("Compare courses for the Applied Math minor", "APPLIED MATHEMATICS (BS)", True),
    ("Compare courses for the Applied Math minor", "APPLIED MATHEMATICS (MINOR)", False),
    ("Plan my Economics major", "Economics (Minor)", True),
    ("Compare the Economics major and minor", "Economics (Minor)", False),
    ("Plan my Economics courses", "Economics (BA)", False),
])
def test_an_outline_keeps_the_program_kind_the_student_named(brief, title, refused):
    from app.generation.artifact_pipeline import _check_program_kind

    if refused:
        with pytest.raises(ValueError, match="asked about a"):
            _check_program_kind(brief, title)
    else:
        _check_program_kind(brief, title)


def _plan_outline(selected, completed=(), program="Economics (Minor)"):
    return {
        "title": "Plan", "sections": [{
            "id": "draft", "heading": "Draft", "purpose": "Draft.",
            "block_types": ["paragraph"], "source_slots": [],
        }],
        "sheets": [], "charts": [], "program": program,
        "selected_courses": list(selected), "completed_courses": list(completed),
    }


def _econ(db, collection, tmp_path, extra="", program=ECON_MINOR):
    source = import_atlas_sample(db, collection, tmp_path, text=ATLAS_SAMPLE + extra)
    path = _write_programs(tmp_path, ("Economics (Minor)", program))
    import_programs(db, collection_id=collection, path=path)
    db.commit()
    return source


@pytest.mark.parametrize(("prompt", "planned", "draft_courses", "selected"), [
    ("Plan ECON 201 with ECON 101 done", True, [], ["ECON 201"]),
    ("Plan ECON 201 with ECON 101 done", True, ["ECON 101"] * 23, ["ECON 201"]),
    # The grounding never retrieved the record: the planner appends it mid-run.
    ("Plan my semester after Calculus I limits", True, [], ["ECON 201"]),
    ("Make a checklist covering ECON 201 this semester", False, [], []),
    ("Make a checklist covering ECON 201\n\nPublished answer:\nPlan ECON 201 for next semester.",
     False, [], ["ECON 201"]),
])
def test_the_artifact_outline_uses_the_planner_for_term_plans(
        db, collection, tmp_path, prompt, planned, draft_courses, selected):
    source = _econ(db, collection, tmp_path)
    draft = _plan_outline(selected, completed=["ECON 101"] if planned else [])
    if not planned:
        draft["sections"] = outline()["sections"]
    draft["sections"][0]["courses"] = draft_courses
    provider = ArtifactStageProvider(
        outline=draft,
        sections={"term1": [{"note": "Macro theory."}],
                  "s1": [section("ECON 201 — Intermediate Macroeconomics")]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {"prompt": prompt, "sourceIds": [str(source)]},
                       "plan-ordered")
    finished = run_once(store, "plan", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"]
                if block.get("type") == "heading"]
    assert headings == (["Term 1"] if planned else ["Facts"])
    if not planned:
        assert spec["blocks"][-1]["text"] == "ECON 201 — Intermediate Macroeconomics"
        return
    assert "degraded_sections" not in spec.get("provenance", {})
    assert spec["title"] == "Economics (Minor): Term plan"
    assert spec["provenance"]["plan"] is True
    assert spec["provenance"]["program_title"] == "Economics (Minor)"
    course = next(block for block in spec["blocks"] if block["type"] == "list")
    assert course["items"] == ["ECON 201 — Intermediate Macroeconomics"]
    assert any(block.get("text") == "Macro theory." for block in spec["blocks"])


def test_a_plan_outline_falls_back_to_the_programs_required_lines(db, collection, tmp_path):
    """A model that cannot outline a plan still gets a deterministic plan."""
    source = _econ(db, collection, tmp_path)

    class TextOnly:
        def generate(self, request):
            return GenerationResult(text="I could not outline it.", finish_reason="stop",
                                    usage=Usage(100, 20, source="provider"))

    client, store, executor = setup_runtime(db, collection, TextOnly(), tmp_path)
    run_id = start_run(client, "document", {
        "prompt": "Make a plan for the Economics minor covering ECON 201.\n\n"
                  "Published answer:\nUse the program's required courses.",
        "requestText": "Make a plan for the Economics minor.",
        "sourceIds": [str(source)],
    }, "plan-outline-fallback")
    finished = run_once(store, "plan-outline-fallback", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"]
                if block.get("type") == "heading"]
    assert headings and headings[0].startswith("Term ")
    assert spec["title"] == "Economics (Minor): Term plan"
    assert spec["provenance"]["program_title"] == "Economics (Minor)"
    assert spec["provenance"]["outline_fallback"] is True


def test_a_checklist_outline_falls_back_to_the_base_template(db, collection, tmp_path):
    """A model that cannot outline a checklist still gets its base sections."""
    source = _econ(db, collection, tmp_path)

    class NoOutline(ArtifactStageProvider):
        def generate(self, request):
            if request.tools and request.tools[0].name == OUTLINE_TOOL:
                return GenerationResult(text="I could not outline it.",
                                        finish_reason="stop",
                                        usage=Usage(100, 20, source="provider"))
            return super().generate(request)

    provider = NoOutline(sections={
        "requirements": [section("ECON 201 — Intermediate Macroeconomics")],
        "choices": [section("One elective from the Economics list.")],
        "notes": [section("Take ECON 201 before the electives.")],
    })
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {
        "prompt": "Make a checklist for the Economics minor covering ECON 201.\n\n"
                  "Published answer:\nUse the program's requirements.",
        "requestText": "Make a checklist for the Economics minor.",
        "sourceIds": [str(source)],
    }, "checklist-template")
    finished = run_once(store, "checklist-template", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"]
                if block.get("type") == "heading"]
    assert headings == ["Requirements", "Choices and electives", "Notes and next steps"]
    assert spec["provenance"]["template"] == "checklist"
    assert spec["provenance"]["template_fallback"] is True


def test_a_workbook_revision_keeps_its_total_formula_and_chart(db, collection, tmp_path):
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
                       "workbook-revision-base")
    assert run_once(store, "workbook-revision-base", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    prior = client.get(f"/api/artifacts/{artifact_id}").json()

    # The revising model sees headings, not the column schema, and calls every
    # column text. The prior sheet's types are the student's data.
    revise_provider = ArtifactStageProvider(
        edits=[{
            "title": "Credits",
            "operations": [{
                "action": "replace", "section_id": "s1",
                "sheet": {
                    "id": "s1", "name": "Credits", "row_purpose": "One course per row.",
                    "columns": [
                        {"key": "c1", "header": "Course", "type": "text"},
                        {"key": "c2", "header": "Credits", "type": "text"},
                        {"key": "c3", "header": "Total", "type": "text"},
                    ],
                    "source_slots": [1],
                },
            }],
        }],
        sections={"s1": [{"rows": [
            {"values": ["Alpha 101", 4, None], "source_slots": [1]},
            {"values": ["Alpha 102", 3, None], "source_slots": [1]},
        ]}]},
    )
    revise_client, revise_store, revise_executor = setup_runtime(
        db, collection, revise_provider, tmp_path)
    start_run(revise_client, "workbook", {
        "prompt": "Alpha facts: add a second course row and update the total.",
        "sourceIds": [str(source)],
        "artifactId": artifact_id, "expectedRevisionId": prior["revisionId"],
    }, "workbook-revision")
    finished = run_once(revise_store, "workbook-revision", revise_executor)
    assert finished.status.value == "succeeded", finished.error
    revised = revise_client.get(f"/api/artifacts/{artifact_id}").json()
    spec = revised["spec"]
    cells = {(cell["row"], cell["column"]): cell for cell in spec["sheets"][0]["cells"]}
    assert cells[(3, 3)]["formula"] == "=SUM(B2:B3)"
    assert spec["charts"][0]["data_range"] == "B1:B3"
    assert spec["charts"][0]["categories_range"] == "A1:A3"
    assert revised["parentRevisionId"] == prior["revisionId"]


def test_an_html_revision_can_add_a_section_and_keep_the_page(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline={
            "title": "Page", "sections": [{
                "id": "body", "heading": "Page", "purpose": "One page.",
                "block_types": ["paragraph"], "source_slots": [1],
            }], "sheets": [], "charts": [],
        },
        sections={"body": [{"html": "<p>Alpha facts.</p>", "source_slots": [1]}]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "html", {"prompt": "Alpha facts", "sourceIds": [str(source)]},
                       "html-revision-base")
    assert run_once(store, "html-revision-base", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    prior = client.get(f"/api/artifacts/{artifact_id}").json()

    revise_provider = ArtifactStageProvider(
        edits=[{
            "title": "Page",
            "operations": [
                {"action": "keep", "section_id": "body"},
                {"action": "insert_after", "section_id": "body",
                 "section": {"id": "electives", "heading": "Electives",
                             "purpose": "Electives.", "block_types": ["paragraph"],
                             "source_slots": [1]}},
            ],
        }],
        sections={"electives": [{"html": "<p>Choose an elective.</p>",
                                "source_slots": [1]}]},
    )
    revise_client, revise_store, revise_executor = setup_runtime(
        db, collection, revise_provider, tmp_path)
    start_run(revise_client, "html", {
        "prompt": "Alpha facts: add an electives section.", "sourceIds": [str(source)],
        "artifactId": artifact_id, "expectedRevisionId": prior["revisionId"],
    }, "html-revision")
    finished = run_once(revise_store, "html-revision", revise_executor)
    assert finished.status.value == "succeeded", finished.error
    revised = revise_client.get(f"/api/artifacts/{artifact_id}").json()
    assert "Alpha facts." in revised["spec"]["html"]
    assert "Choose an elective." in revised["spec"]["html"]
    assert revised["parentRevisionId"] == prior["revisionId"]


def test_a_workbook_that_asks_for_totals_gets_a_code_built_total_column(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline={
            "title": "Credits", "sections": [],
            "sheets": [{
                "id": "s1", "name": "Credits", "row_purpose": "One course per row.",
                "columns": [
                    {"key": "course", "header": "Course", "type": "text"},
                    {"key": "credits", "header": "Credits", "type": "number"},
                ],
                "source_slots": [1],
            }],
            "charts": [],
        },
        sections={"s1": [{"rows": [
            {"values": ["Alpha 101", 4], "source_slots": [1]},
            {"values": ["Alpha 102", 3], "source_slots": [1]},
        ]}]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "workbook",
                       {"prompt": "Alpha facts with totals", "sourceIds": [str(source)]},
                       "workbook-totals")
    assert run_once(store, "workbook", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    cells = {(cell["row"], cell["column"]): cell for cell in spec["sheets"][0]["cells"]}
    assert cells[(1, 3)]["value"] == "Total"
    assert cells[(3, 3)]["formula"] == "=SUM(B2:B3)"


def test_a_workbook_row_whose_number_column_is_not_a_number_is_refused(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(
        outline={
            "title": "Credits", "sections": [],
            "sheets": [{
                "id": "s1", "name": "Credits", "row_purpose": "One course per row.",
                "columns": [
                    {"key": "course", "header": "Course", "type": "text"},
                    {"key": "credits", "header": "Credits", "type": "number"},
                ],
                "source_slots": [1],
            }],
            "charts": [],
        },
        sections={"s1": [
            {"rows": [{"values": ["Alpha 101", "4 credits"], "source_slots": [1]}]},
            {"rows": [{"values": ["Alpha 101", 4], "source_slots": [1]},
                      {"values": ["Alpha 102", None], "source_slots": [1]}]},
        ]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    start_run(client, "workbook", {"prompt": "Alpha facts", "sourceIds": [str(source)]},
              "workbook-bad-number")
    finished = run_once(store, "workbook", executor)
    assert finished.status.value == "succeeded", finished.error
    assert any("is not a number" in str(message)
               for request in provider.requests for message in request.messages)


def test_a_plan_order_violation_names_its_section(db, collection, tmp_path):
    _econ(db, collection, tmp_path)
    from app.artifacts.checks import check_generated_artifact
    from app.artifacts.storage import validate_spec

    spec = validate_spec("document", {"kind": "document", "title": "Plan", "blocks": [
        {"id": "t1:h", "type": "heading", "level": 2, "text": "Term 1"},
        {"id": "t1:c", "type": "list", "items": [
            "ECON 201 — Intermediate Macroeconomics",
            "ECON 101 — Principles of Macroeconomics",
        ]},
    ]})
    findings = check_generated_artifact(
        db, collection_id=collection, spec=spec,
        known_courses={"ECON 201", "ECON 101"}, campus_equivalents={},
        plan_terms={"ECON 201": 1, "ECON 101": 1},
        plan_term_sections={1: "t1"},
        section_texts={"t1": "ECON 201 — Intermediate Macroeconomics ECON 101 — "
                            "Principles of Macroeconomics"})
    k6 = [finding for finding in findings if finding.check == "K6"]
    assert k6 and k6[0].section_id == "t1"
    assert "ECON 201" in k6[0].message


def test_a_plan_request_is_not_turned_into_a_checklist_by_the_model_brief(db, collection, tmp_path):
    source = _econ(db, collection, tmp_path)
    provider = ArtifactStageProvider(
        outline=_plan_outline(["ECON 201"], completed=["ECON 101"]),
        sections={"term1": [{"note": "Macro theory."}]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document", {
        "prompt": "Checklist for the Economics minor\n\nPublished answer:\nECON 201 follows ECON 101.",
        "requestText": "Plan ECON 201 with ECON 101 done.",
        "sourceIds": [str(source)],
    }, "plan-not-checklist")
    assert run_once(store, "plan", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    headings = [block.get("text") for block in spec["blocks"] if block.get("type") == "heading"]
    assert "Term 1" in headings
    assert spec["title"] == "Economics (Minor): Term plan"


@pytest.mark.parametrize(("text", "credits", "courses", "terms"), [
    ("Plan my Biology BS.", 16.0, 5, None),
    ("No more than 12 credits in any term, 3 courses a term, finish in 4 semesters.",
     12.0, 3, 4),
    ("A four-year plan with at most 15 credit hours.", 15.0, 5, 8),
    ("Make a 3-year spreadsheet plan.", 16.0, 5, 6),
])
def test_the_student_term_limits_are_read(text, credits, courses, terms):
    from app.generation.artifact_pipeline import (
        _requested_course_cap,
        _requested_credit_cap,
        _requested_terms,
    )

    assert _requested_credit_cap(text) == credits
    assert _requested_course_cap(text) == courses
    assert _requested_terms(text) == terms


@pytest.mark.parametrize(("written", "printed"), [
    ("BIO 141 and QTM 150", "BIOL 141 and DATASCI 150"),
    ("PSYCH 110", "PSYC 110"),
    ("MATH_OX 111 stays Oxford", "MATH_OX 111 stays Oxford"),
])
def test_artifact_text_prints_the_atlas_course_code(written, printed):
    from app.atlas.display import canonicalize_codes

    assert canonicalize_codes(written) == printed


@pytest.mark.parametrize("note", ["MATH 111 is scheduled here.", "Take this term lightly."])
@pytest.mark.parametrize("kind", ["document", "workbook"])
def test_a_plan_note_cannot_move_a_course_between_terms(db, collection, tmp_path, kind, note):
    """A note naming a course, or not copied from a slot, is dropped in the same call."""
    source = _econ(db, collection, tmp_path)
    draft = _plan_outline(["MATH 111", "MATH 112"])
    if kind == "workbook":
        draft["sections"] = []
        draft["sheets"] = [{
            "id": "draft", "name": "Draft", "row_purpose": "One course per row.",
            "columns": [{"key": "course", "header": "Course"}],
        }]
    provider = ArtifactStageProvider(
        outline=draft,
        sections={"term1": [{"note": "Limits."}],
                  "term2": [{"note": note}]},
    )
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, kind,
                       {"prompt": "Make a semester plan starting with MATH 111 for the Economics minor",
                        "sourceIds": [str(source)]}, "plan-moved")
    finished = run_once(store, "plan", executor)
    assert finished.status.value == "succeeded", finished.error
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    from app.artifacts.checks import extract_planned_terms

    assert extract_planned_terms(spec) == {"MATH 111": 1, "MATH 112": 2}
    assert note.rstrip(".") not in json.dumps(spec)
    assert "degraded_sections" not in spec.get("provenance", {})
    if kind == "document":
        lines = {block["items"][0]: block["id"] for block in spec["blocks"]
                 if block["type"] == "list"}
    else:
        lines = {cell["value"]: f"{sheet['id']}/{cell['id']}"
                 for sheet in spec["sheets"] for cell in sheet["cells"] if cell["row"] == 2}
    assert set(lines) == {"MATH 111 — Calculus I", "MATH 112 — Calculus II"}
    for line, element_id in lines.items():
        record = line.replace(" — ", ": ")
        assert [ref["text_snapshot"].splitlines()[0]
                for ref in spec["evidence"][element_id]] == [record]


def test_a_failed_plan_section_falls_back_to_the_fixed_course_list(db, collection, tmp_path):
    source = _econ(db, collection, tmp_path)

    truncated = GenerationResult(text="", finish_reason="length",
                                 usage=Usage(100, 30, source="provider"))
    provider = ArtifactStageProvider(
        outline=_plan_outline(["MATH 111", "MATH 112"]),
        sections={"term1": [truncated], "term2": [truncated]},
    )
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
    # Each fallback course list cites the records of its own courses.
    lists = [block for block in spec["blocks"] if block["type"] == "list"]
    assert [block["items"] for block in lists] == [
        ["MATH 111 — Calculus I"], ["MATH 112 — Calculus II"],
    ]
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


def test_a_section_reply_in_prose_is_asked_for_the_tool(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    prose = GenerationResult(text="I wrote the section in the reply instead.",
                             finish_reason="stop", usage=Usage(100, 30, source="provider"))
    provider = ArtifactStageProvider(
        outline=outline(), sections={"s1": [prose, section()]})
    client, store, executor = setup_runtime(db, collection, provider, tmp_path)
    run_id = start_run(client, "document",
                       {"prompt": "Alpha", "sourceIds": [str(source)]}, "prose-section")
    assert run_once(store, "prose-section", executor).status.value == "succeeded"
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get(f"/api/artifacts/{artifact_id}").json()["spec"]
    assert any("Alpha facts." in block.get("text", "") for block in spec["blocks"])
    assert "did not call the required tool" in message_text(provider.requests[-1].messages[-1])


def test_a_blocked_course_goes_in_the_advisor_section(db, collection, tmp_path):
    source = _econ(
        db, collection, tmp_path,
        extra='\n{"code": "ECON 499", "title": "Independent Study", '
              '"description": "Permission only.", "requisites": "Instructor permission required."}',
        program="Economics (Minor)\nDEGREE AWARDED N/A\nRequirements\n• Econ 499\n",
    )
    provider = ArtifactStageProvider(
        outline=_plan_outline(["ECON 499"]),
        sections={"advisor": [{"note": "Permission only."}]},
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
    assert any("ECON 499 — Independent Study" in item and "unparsed" in item
               for block in spec["blocks"] for item in block.get("items", []))


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


@pytest.mark.parametrize(("rows", "known", "flagged"), [
    # A heading cell next to its course count is not a course ("Physics" and "12").
    ([["Section A — Physics", "12"]], set(), {}),
    # An unknown code is flagged and owned by the section, however the section spells it.
    ([["POLS387: Independent Research", "4"]], set(), {"POLS 387": "s1"}),
    ([["Math_OX 112", "Calculus II"]], set(), {"MATH_OX 112": "s1"}),
    ([["MATH 111", "Calculus I"]], {"MATH 111"}, {}),
])
def test_the_course_check_reads_each_piece_and_names_its_section(rows, known, flagged):
    from app.artifacts.checks import check_generated_artifact
    from app.artifacts.storage import validate_spec

    spec = validate_spec("document", {"kind": "document", "title": "Checklist", "blocks": [
        {"id": "s1:t", "type": "table", "headers": ["Course", "Hours"], "rows": rows}]})
    # The pipeline's own section text joins a table's cells with spaces.
    findings = check_generated_artifact(
        None, collection_id=None, spec=spec, known_courses=known, campus_equivalents={},
        section_texts={"s1": " ".join(cell for row in rows for cell in row)})
    assert {finding.message.split(" is not")[0]: finding.section_id
            for finding in findings if finding.check == "K4"} == flagged
