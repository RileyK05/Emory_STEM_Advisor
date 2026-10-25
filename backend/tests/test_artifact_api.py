"""Native file round-trips and revision privacy/CAS against real Postgres."""

import io
import json
from datetime import date
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.artifacts.models import ArtifactReference, DocumentSpec, HtmlArtifactSpec, WorkbookSpec
from app.artifacts.storage import ArtifactNotFound, ArtifactStore
from tests.helpers import api_client


def spec(text="Original"):
    return {"kind": "document", "title": "Native document",
            "blocks": [{"id": "p1", "type": "paragraph", "text": text}]}


def test_create_revise_old_revision_preview_and_export(db, collection):
    client = api_client(db, collection)
    created = client.post("/api/artifacts", json={"kind": "document", "spec": spec()})
    assert created.status_code == 200
    artifact = created.json()
    changed = client.post(f"/api/artifacts/{artifact['artifactId']}/revisions", json={
        "expectedParentRevisionId": artifact["revisionId"], "draftId": "draft-1",
        "operations": [{"type": "replace_block", "block_id": "p1",
                        "block": {"id": "p1", "type": "paragraph", "text": "Updated"}}],
    })
    assert changed.status_code == 200
    revision = changed.json()
    assert revision["parentRevisionId"] == artifact["revisionId"]
    old = client.get(f"/api/artifacts/{artifact['artifactId']}",
                     params={"revisionId": artifact["revisionId"]}).json()
    assert old["spec"]["blocks"][0]["text"] == "Original"
    exported = client.get(f"/api/artifacts/{artifact['artifactId']}/download",
                          params={"format": "md", "revisionId": revision["revisionId"]})
    assert exported.status_code == 200 and "Updated" in exported.text
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.headers["x-artifact-revision"] == revision["revisionId"]


def test_stale_save_fails_and_draft_retry_is_idempotent(db, collection):
    client = api_client(db, collection)
    artifact = client.post("/api/artifacts", json={"kind": "document", "spec": spec()}).json()
    body = {"expectedParentRevisionId": artifact["revisionId"], "draftId": "d1", "spec": spec("New")}
    endpoint = f"/api/artifacts/{artifact['artifactId']}/revisions"
    first = client.post(endpoint, json=body)
    second = client.post(endpoint, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["revisionId"] == second.json()["revisionId"]
    stale = client.post(endpoint, json={**body, "draftId": "d2", "spec": spec("Lost")})
    assert stale.status_code == 409
    head = client.get(f"/api/artifacts/{artifact['artifactId']}").json()
    assert head["spec"]["blocks"][0]["text"] == "New"


def test_private_workspace_lookup_does_not_expose_other_artifact(db, tmp_path):
    mine = ArtifactStore(db, workspace_key="alice", data_dir=tmp_path)
    other = ArtifactStore(db, workspace_key="bob", data_dir=tmp_path)
    record = mine.create(DocumentSpec.model_validate(spec()))
    with pytest.raises(ArtifactNotFound):
        other.get(record["artifact_id"])
    assert other.list() == []


def test_import_original_is_preserved_byte_for_byte(db, collection):
    client = api_client(db, collection)
    original = b"# A document\n\nOriginal source text."
    imported = client.post("/api/artifacts/import",
                           files={"file": ("source.md", original, "text/markdown")})
    assert imported.status_code == 200
    artifact = imported.json()
    raw = client.get(f"/api/artifacts/{artifact['artifactId']}/original")
    assert raw.content == original
    assert artifact["original"]["preserved"] is True


def test_workbook_literal_equals_and_dates_survive_json_persistence(db, collection):
    client = api_client(db, collection)
    body = {"kind": "workbook", "spec": {"kind": "workbook", "title": "Workbook",
            "sheets": [{"id": "s1", "name": "Data", "cells": [
                {"id": "c1", "row": 1, "column": 1, "value": "=1+1"},
                {"id": "c2", "row": 2, "column": 1, "value": "2026-10-07", "value_type": "date"},
            ]}]}}
    saved = client.post("/api/artifacts", json=body)
    assert saved.status_code == 200
    artifact = saved.json()
    exported = client.get(f"/api/artifacts/{artifact['artifactId']}/download",
                          params={"format": "xlsx", "revisionId": artifact["revisionId"]})
    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.active["A1"].value == "=1+1"
    assert workbook.active["A1"].data_type == "s"
    assert workbook.active["A2"].value.date() == date(2026, 10, 7)


def test_wrong_revision_is_not_found(db, collection):
    client = api_client(db, collection)
    created = client.post("/api/artifacts", json={"kind": "document", "spec": spec()}).json()
    resp = client.get(f"/api/artifacts/{created['artifactId']}", params={"revisionId": str(uuid4())})
    assert resp.status_code == 404


def test_malformed_and_nonfinite_specs_are_rejected(db, collection):
    client = api_client(db, collection)
    resp = client.post("/api/artifacts", json={"kind": "document", "spec": {**spec(), "surprise": "bad"}})
    assert resp.status_code == 422


def test_manual_artifacts_cannot_claim_client_supplied_evidence_or_authority(db, collection):
    client = api_client(db, collection)
    forged = {"chunk_id": str(uuid4()), "source_id": str(uuid4()),
              "source_name": "invented.txt", "locator_label": "page 99",
              "text_snapshot": "invented citation"}
    response = client.post("/api/artifacts", json={"kind": "document", "spec": {
        **spec(), "evidence": {"p1": [forged]},
        "provenance": {"authority": "verified_source", "admin": True},
    }})
    assert response.status_code == 200
    saved = response.json()["spec"]
    assert saved["evidence"] == {}
    assert saved["provenance"]["authority"] == "manual_draft"
    assert saved["provenance"]["manual_edit"] is True
    assert "p1" in saved["provenance"]["unverifiedElements"]


def test_manual_revision_retains_evidence_only_for_unchanged_elements(db, collection, tmp_path):
    client = api_client(db, collection)
    ref = ArtifactReference(chunk_id=uuid4(), source_id=uuid4(), source_name="real.txt",
                            locator_label="paragraph 1", text_snapshot="trusted excerpt")
    original = DocumentSpec.model_validate({
        "kind": "document", "title": "Evidence", "blocks": [
            {"id": "keep", "type": "paragraph", "text": "unchanged"},
            {"id": "edit", "type": "paragraph", "text": "before"},
        ], "evidence": {"keep": [ref], "edit": [ref]},
        "provenance": {"authority": "generated_draft"},
    })
    trusted = ArtifactStore(db, workspace_key="development", data_dir=tmp_path).create(original)
    changed = client.post(f"/api/artifacts/{trusted['artifact_id']}/revisions", json={
        "expectedParentRevisionId": str(trusted["revision_id"]),
        "spec": {"kind": "document", "title": "Edited", "blocks": [
            {"id": "keep", "type": "paragraph", "text": "unchanged"},
            {"id": "edit", "type": "paragraph", "text": "after"},
            {"id": "new", "type": "paragraph", "text": "added"},
        ], "evidence": {"edit": [ref.model_dump(mode="json")],
                       "new": [ref.model_dump(mode="json")]},
        "provenance": {"authority": "verified_source"}},
    })
    assert changed.status_code == 200, changed.text
    result = changed.json()["spec"]
    assert set(result["evidence"]) == {"keep"}
    assert result["evidence"]["keep"][0]["chunk_id"] == str(ref.chunk_id)
    assert result["provenance"]["authority"] == "manual_draft"
    assert set(json.loads(result["provenance"]["unverifiedElements"])) == {
        "edit", "new"
    }


def test_manual_revision_drops_evidence_for_deleted_elements(db, collection, tmp_path):
    client = api_client(db, collection)
    ref = ArtifactReference(chunk_id=uuid4(), source_id=uuid4(), source_name="real.txt",
                            locator_label="paragraph 1", text_snapshot="trusted excerpt")
    original = DocumentSpec.model_validate({"kind": "document", "title": "Evidence",
        "blocks": [{"id": "gone", "type": "paragraph", "text": "remove"}],
        "evidence": {"gone": [ref]}})
    trusted = ArtifactStore(db, workspace_key="development", data_dir=tmp_path).create(original)
    changed = client.post(f"/api/artifacts/{trusted['artifact_id']}/revisions", json={
        "expectedParentRevisionId": str(trusted["revision_id"]),
        "spec": {"kind": "document", "title": "Evidence", "blocks": [],
                 "evidence": {"gone": [ref.model_dump(mode="json")]}},
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["spec"]["evidence"] == {}


def test_manual_workbook_revision_keeps_only_unchanged_cell_evidence(db, collection, tmp_path):
    client = api_client(db, collection)
    ref = ArtifactReference(chunk_id=uuid4(), source_id=uuid4(), source_name="real.txt",
                            locator_label="table row 1", text_snapshot="trusted value")
    spec = {"kind": "workbook", "title": "Evidence", "sheets": [{"id": "s1",
        "name": "Data", "cells": [
            {"id": "keep", "row": 1, "column": 1, "value": "same"},
            {"id": "edit", "row": 2, "column": 1, "value": "before"},
        ]}], "evidence": {"s1/keep": [ref.model_dump(mode="json")],
                        "s1/edit": [ref.model_dump(mode="json")]}}
    trusted = ArtifactStore(db, workspace_key="development", data_dir=tmp_path).create(
        WorkbookSpec.model_validate(spec)
    )
    response = client.post(f"/api/artifacts/{trusted['artifact_id']}/revisions", json={
        "expectedParentRevisionId": str(trusted["revision_id"]),
        "operations": [{"type": "set_cell", "sheet_id": "s1", "cell_id": "edit",
                        "cell": {"id": "edit", "row": 2, "column": 1, "value": "after"}}],
    })
    assert response.status_code == 200, response.text
    saved = response.json()["spec"]
    assert set(saved["evidence"]) == {"s1/keep"}
    assert "s1/edit" in json.loads(saved["provenance"]["unverifiedElements"])


def test_html_manual_revision_clears_changed_html_evidence(db, collection, tmp_path):
    client = api_client(db, collection)
    ref = ArtifactReference(chunk_id=uuid4(), source_id=uuid4(), source_name="real.txt",
                            locator_label="paragraph 1", text_snapshot="trusted html text")
    trusted = ArtifactStore(db, workspace_key="development", data_dir=tmp_path).create(
        HtmlArtifactSpec(kind="html", title="Page", html="<p>before</p>",
                         evidence={"html": [ref]}, provenance={"authority": "generated_draft"})
    )
    response = client.post(f"/api/artifacts/{trusted['artifact_id']}/revisions", json={
        "expectedParentRevisionId": str(trusted["revision_id"]),
        "spec": {"kind": "html", "title": "Page", "html": "<p>after</p>",
                 "evidence": {"html": [ref.model_dump(mode="json")]},
                 "provenance": {"authority": "verified_source"}},
    })
    assert response.status_code == 200, response.text
    saved = response.json()["spec"]
    assert saved["evidence"] == {}
    assert saved["provenance"]["authority"] == "manual_draft"
    assert saved["provenance"]["unverifiedElements"] == '["html"]'


def test_artifact_count_and_revision_caps_are_enforced(db, tmp_path):
    from app.artifacts.errors import ArtifactLimitError

    store = ArtifactStore(db, workspace_key="development", data_dir=tmp_path,
                          max_artifacts=1, max_revisions=1)
    first = store.create(HtmlArtifactSpec(title="One", html="<p>one</p>"))
    with pytest.raises(ArtifactLimitError):
        store.create(HtmlArtifactSpec(title="Two", html="<p>two</p>"))
    with pytest.raises(ArtifactLimitError):
        store.revise(first["artifact_id"], first["revision_id"],
                     HtmlArtifactSpec(title="One", html="<p>changed</p>"))
