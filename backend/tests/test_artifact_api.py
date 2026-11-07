"""Native file round-trips and revision privacy/CAS against real Postgres."""

import io
from datetime import date
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.artifacts.models import DocumentSpec, HtmlArtifactSpec
from app.artifacts.storage import (
    ArtifactNotFound,
    ArtifactStore,
    RevisionConflict,
    public_record,
    validate_spec,
)
from tests.helpers import api_client


def spec(text="Original"):
    return {"kind": "document", "title": "Native document",
            "blocks": [{"id": "p1", "type": "paragraph", "text": text}]}


def _store(client, db):
    return ArtifactStore(db, workspace_key="development",
                         data_dir=client.app.state.settings.data_dir)


def _saved(client, db, payload=None, kind="document"):
    return public_record(_store(client, db).create(validate_spec(kind, payload or spec())))


def test_revise_old_revision_preview_and_export(db, collection):
    client = api_client(db, collection)
    artifact = _saved(client, db)
    revision = public_record(_store(client, db).revise(
        artifact["artifactId"], artifact["revisionId"],
        validate_spec("document", spec("Updated")), draft_id="draft-1"))
    assert revision["parentRevisionId"] == artifact["revisionId"]
    old = client.get(f"/api/artifacts/{artifact['artifactId']}",
                     params={"revisionId": artifact["revisionId"]}).json()
    assert old["spec"]["blocks"][0]["text"] == "Original"
    exported = client.get(f"/api/artifacts/{artifact['artifactId']}/download",
                          params={"format": "md", "revisionId": revision["revisionId"]})
    assert exported.status_code == 200 and "Updated" in exported.text
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.headers["x-artifact-revision"] == revision["revisionId"]


def test_stale_save_fails(db, collection):
    client = api_client(db, collection)
    artifact = _saved(client, db)
    store = _store(client, db)

    def save(text, draft):
        return store.revise(artifact["artifactId"], artifact["revisionId"],
                            validate_spec("document", spec(text)), draft_id=draft)

    save("New", "d1")
    with pytest.raises(RevisionConflict):
        save("Lost", "d2")
    db.rollback()
    head = client.get(f"/api/artifacts/{artifact['artifactId']}").json()
    assert head["spec"]["blocks"][0]["text"] == "New"


def test_private_workspace_lookup_does_not_expose_other_artifact(db, tmp_path):
    mine = ArtifactStore(db, workspace_key="alice", data_dir=tmp_path)
    other = ArtifactStore(db, workspace_key="bob", data_dir=tmp_path)
    record = mine.create(DocumentSpec.model_validate(spec()))
    with pytest.raises(ArtifactNotFound):
        other.get(record["artifact_id"])
    assert other.list() == []


def test_workbook_literal_equals_and_dates_survive_json_persistence(db, collection):
    client = api_client(db, collection)
    artifact = _saved(client, db, {"kind": "workbook", "title": "Workbook",
                                   "sheets": [{"id": "s1", "name": "Data", "cells": [
                {"id": "c1", "row": 1, "column": 1, "value": "=1+1"},
                {"id": "c2", "row": 2, "column": 1, "value": "2026-10-07", "value_type": "date"},
            ]}]}, kind="workbook")
    exported = client.get(f"/api/artifacts/{artifact['artifactId']}/download",
                          params={"format": "xlsx", "revisionId": artifact["revisionId"]})
    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.active["A1"].value == "=1+1"
    assert workbook.active["A1"].data_type == "s"
    assert workbook.active["A2"].value.date() == date(2026, 10, 7)


def test_wrong_revision_is_not_found(db, collection):
    client = api_client(db, collection)
    created = _saved(client, db)
    resp = client.get(f"/api/artifacts/{created['artifactId']}", params={"revisionId": str(uuid4())})
    assert resp.status_code == 404


def test_malformed_specs_are_rejected():
    with pytest.raises(ValueError):
        validate_spec("document", {**spec(), "surprise": "bad"})


@pytest.mark.parametrize("cap_kind", ["count", "storage_bytes"])
def test_artifact_count_and_revision_caps_are_enforced(db, tmp_path, cap_kind):
    from app.artifacts.errors import ArtifactLimitError
    from app.db import queries
    from app.quotas.service import WORKSPACE_STORAGE_FULL_MESSAGE, spec_storage_bytes

    first_spec = HtmlArtifactSpec(title="One", html="<p>one</p>")
    if cap_kind == "count":
        store = ArtifactStore(
            db, workspace_key="development", data_dir=tmp_path, max_artifacts=1, max_revisions=1
        )
    else:
        store = ArtifactStore(
            db,
            workspace_key="development",
            data_dir=tmp_path,
            max_storage_bytes=spec_storage_bytes(first_spec) + 16,
        )
    first = store.create(first_spec)
    with pytest.raises(ArtifactLimitError) as create_exc:
        store.create(HtmlArtifactSpec(title="Two", html="<p>two</p>"))
    with pytest.raises(ArtifactLimitError) as revise_exc:
        store.revise(
            first["artifact_id"],
            first["revision_id"],
            HtmlArtifactSpec(title="One", html="<p>changed</p>"),
        )
    if cap_kind == "storage_bytes":
        assert str(create_exc.value) == WORKSPACE_STORAGE_FULL_MESSAGE
        assert str(revise_exc.value) == WORKSPACE_STORAGE_FULL_MESSAGE
        db.execute(
            queries.get("delete_artifact_for_workspace"),
            {"artifact_id": first["artifact_id"], "workspace_key": "development"},
        )
        db.commit()
        reclaimed = store.create(HtmlArtifactSpec(title="Two", html="<p>two</p>"))
        assert reclaimed["spec"]["title"] == "Two"
