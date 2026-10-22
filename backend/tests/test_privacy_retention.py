"""Tests for data retention, student export, and fenced deletion (Item 16)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.artifacts.models import DocumentSpec, ParagraphBlock
from app.artifacts.storage import ArtifactStore
from app.conversations.store import ConversationStore
from app.db import queries
from app.privacy.service import PrivacyService, privacy_export_digest
from app.util.localpath import io_path
from tests.helpers import api_client


def _setup_workspace_data(db, workspace_key: str, data_dir: Path | None = None):
    user_id = uuid4()
    db.execute(
        queries.get("upsert_app_user"),
        {
            "user_id": user_id,
            "issuer": "https://auth.emory.edu",
            "subject": str(user_id),
            "workspace_key": workspace_key,
            "display_name": f"Student {user_id}",
            "email": f"{user_id}@emory.edu",
            "bootstrap": False,
        },
    )

    # 1. Create conversation with turns
    conv_store = ConversationStore(db, workspace_key)
    conv = conv_store.create("Graduation Checklist")
    conv_id = UUID(conv["conversationId"])
    req_id = uuid4()
    conv_store.begin(conv_id, req_id, 0, {"query": "What are my math requirements?"})
    conv_store.finish(
        conv_id,
        req_id,
        {
            "type": "answer",
            "answer": "You need Math 111 and Math 112 [1].",
            "citations": [],
            "trace": {},
        },
    )

    # 2. Create artifact with revision
    art_store = ArtifactStore(db, workspace_key=workspace_key, data_dir=data_dir or Path("/tmp"))
    spec = DocumentSpec(
        title="Degree Plan",
        blocks=[ParagraphBlock(id="p1", text="Math major requirements.")],
    )
    artifact = art_store.create(spec)
    art_id = artifact["artifact_id"]

    db.commit()
    return user_id, conv_id, art_id


def test_privacy_export_manifest_and_inventory(db, tmp_path):
    ws = "ws-export-test"
    user_id, conv_id, art_id = _setup_workspace_data(db, ws, tmp_path)

    export_res = PrivacyService.start_export(db, ws, user_id=user_id)
    assert export_res["status"] == "completed"
    assert export_res["workspaceKey"] == ws
    assert export_res["downloadToken"] is not None

    manifest = export_res["manifest"]
    assert manifest["inventory"]["conversations"] == 1
    assert manifest["inventory"]["artifacts"] == 1
    assert len(manifest["data"]["conversations"]) == 1
    assert manifest["data"]["conversations"][0]["conversationId"] == str(conv_id)
    assert len(manifest["data"]["artifacts"]) == 1
    assert manifest["data"]["artifacts"][0]["artifact_id"] == str(art_id)

    # Get export
    fetched = PrivacyService.get_export(db, UUID(export_res["exportId"]), ws)
    assert fetched["exportId"] == export_res["exportId"]

    # List exports
    listed = PrivacyService.list_exports(db, ws)
    assert len(listed) >= 1
    assert any(e["exportId"] == export_res["exportId"] for e in listed)


def test_export_interrupted_by_deletion_cannot_publish_a_private_bundle(db, tmp_path, monkeypatch):
    workspace = "export-deleted-during-read"
    user_id, _, _ = _setup_workspace_data(db, workspace, tmp_path)
    original_turns = ConversationStore.turns

    def delete_after_read(store, *args, **kwargs):
        turns = original_turns(store, *args, **kwargs)
        PrivacyService.delete_workspace(db, workspace, user_id=user_id, data_dir=tmp_path)
        return turns

    monkeypatch.setattr(ConversationStore, "turns", delete_after_read)
    with pytest.raises(HTTPException) as gone:
        PrivacyService.start_export(db, workspace, user_id=user_id)
    assert gone.value.status_code == 410
    assert PrivacyService.list_exports(db, workspace) == []


def test_privacy_deletion_fences_and_deletes_dependency_order(db, tmp_path):
    ws = "ws-delete-test"
    user_id, _conv_id, _art_id = _setup_workspace_data(db, ws, tmp_path)

    # Check inventory before deletion
    inv_before = dict(
        db.execute(queries.get("count_workspace_inventory"), {"workspace_key": ws}).fetchone()
    )
    assert inv_before["conversations"] == 1
    assert inv_before["artifacts"] == 1

    # Perform deletion
    del_res = PrivacyService.delete_workspace(db, ws, user_id=user_id, reason="Student requested erasure")
    assert del_res["status"] == "completed"
    assert del_res["deletedCounts"]["conversations"] == 1
    assert del_res["deletedCounts"]["artifacts"] == 1

    # Verify workspace inventory is zero
    inv_after = dict(
        db.execute(queries.get("count_workspace_inventory"), {"workspace_key": ws}).fetchone()
    )
    assert inv_after["conversations"] == 0
    assert inv_after["artifacts"] == 0

    # Verify tombstone exists
    tombstone = db.execute(
        queries.get("check_deletion_tombstone"),
        {"workspace_key": ws, "resource_type": "workspace", "resource_id": ws},
    ).fetchone()
    assert tombstone is not None
    assert tombstone["reason"] == "Student requested erasure"

    # Exporting a deleted workspace raises 410 Gone
    with pytest.raises(HTTPException) as gone:
        PrivacyService.start_export(db, ws, user_id=user_id)
    assert gone.value.status_code == 410


@pytest.mark.parametrize("write", ["record", "conversation", "job", "execution", "feedback"])
def test_deleted_workspaces_cannot_recreate_private_data(db, migrated_dsn, tmp_path, write):
    from app.auth.capabilities import require_workspace_execution
    from app.auth.models import Principal
    from app.db.connection import connect
    from app.gaps.feedback import FeedbackService
    from app.jobs.store import JobStore
    from app.records.service import AcademicRecordService

    workspace = f"deleted-{write}"
    user_id, _, _ = _setup_workspace_data(db, workspace, tmp_path)
    actor = Principal(workspace_key=workspace, user_id=user_id, role="student",
                      issuer="https://auth.emory.edu", subject=str(user_id))
    PrivacyService.delete_workspace(db, workspace, user_id=user_id, data_dir=tmp_path)
    with pytest.raises(HTTPException) as blocked:
        if write == "record":
            AcademicRecordService.get_or_create_record(db, workspace)
        elif write == "conversation":
            ConversationStore(db, workspace).create("After deletion")
        elif write == "job":
            JobStore(lambda: connect(migrated_dsn)).create(
                kind="artifact.run", scope_key=workspace, idempotency_key="after-delete", payload={})
        elif write == "feedback":
            FeedbackService.submit(db, workspace, uuid4(), "up", "helpful", None, actor)
        else:
            require_workspace_execution(db, workspace, lock=True)
    assert blocked.value.status_code == 410


def test_privacy_deletion_removes_traces_responses_and_debug_experiments(
    db, collection, tmp_path
):
    ws = "ws-delete-traces"
    user_id, conversation_id, _ = _setup_workspace_data(db, ws, tmp_path)
    trace_id = uuid4()
    response_id = uuid4()
    experiment_id = uuid4()
    db.execute(
        """
        INSERT INTO retrieval_traces (
            trace_id, collection_id, workspace_key, query, normalized_query,
            config_version, refused
        ) VALUES (%s, %s, %s, %s, %s, 1, false)
        """,
        (trace_id, collection, ws, "What are my requirements?", "what are my requirements"),
    )
    db.execute(
        """
        INSERT INTO responses (
            response_id, trace_id, status, text, prompt_version, context_snapshot
        ) VALUES (%s, %s, 'answer', 'A saved answer.', 'test', '{}'::jsonb)
        """,
        (response_id, trace_id),
    )
    db.execute(
        """
        INSERT INTO debug_experiments (
            experiment_id, workspace_key, source_conversation_id,
            source_request_id, snapshot, state
        ) VALUES (%s, %s, %s, %s, '{}'::jsonb, 'recorded')
        """,
        (experiment_id, ws, conversation_id, uuid4()),
    )
    db.commit()

    PrivacyService.delete_workspace(db, ws, user_id=user_id, data_dir=tmp_path)

    assert db.execute(
        "SELECT 1 FROM retrieval_traces WHERE trace_id = %s", (trace_id,)
    ).fetchone() is None
    assert db.execute(
        "SELECT 1 FROM responses WHERE response_id = %s", (response_id,)
    ).fetchone() is None
    assert db.execute(
        "SELECT 1 FROM debug_experiments WHERE experiment_id = %s", (experiment_id,)
    ).fetchone() is None


def test_cross_owner_files_survive_deletion(db, tmp_path):
    ws_a = "ws-owner-a"
    ws_b = "ws-owner-b"
    _setup_workspace_data(db, ws_a, tmp_path)
    _user_b, conv_b, art_b = _setup_workspace_data(db, ws_b, tmp_path)

    # Delete workspace A
    PrivacyService.delete_workspace(db, ws_a, reason="Delete A")

    # Workspace B data must remain completely intact
    inv_b = dict(
        db.execute(queries.get("count_workspace_inventory"), {"workspace_key": ws_b}).fetchone()
    )
    assert inv_b["conversations"] == 1
    assert inv_b["artifacts"] == 1

    conv_store_b = ConversationStore(db, ws_b)
    assert conv_store_b.get(conv_b) is not None

    art_store_b = ArtifactStore(db, workspace_key=ws_b, data_dir=tmp_path)
    assert art_store_b.get(art_b) is not None


def test_deletion_api_endpoint_requires_confirmation(db, collection, tmp_path):
    ws = "ws-api-privacy"
    _setup_workspace_data(db, ws, tmp_path)
    client = api_client(db, collection)

    # 1. Invalid confirmation string raises 422
    bad_res = client.post(
        "/api/privacy/deletions",
        json={"confirmation": "please delete", "reason": "test"},
    )
    assert bad_res.status_code == 422
    assert "DELETE MY DATA" in bad_res.json()["detail"]

    # 2. Correct confirmation string executes deletion
    good_res = client.post(
        "/api/privacy/deletions",
        json={"confirmation": "DELETE MY DATA", "reason": "Graduated student"},
    )
    assert good_res.status_code == 200
    data = good_res.json()
    assert data["status"] == "completed"
    assert "deleted" in data["message"].lower()


def test_owner_download_hides_token_and_inventory_covers_student_domains(db, collection, tmp_path):
    ws = "development"
    _setup_workspace_data(db, ws, tmp_path)
    db.execute(
        queries.get("get_or_create_academic_record"),
        {"record_id": uuid4(), "workspace_key": ws},
    )
    db.execute(
        queries.get("insert_response_feedback"),
        {
            "feedback_id": uuid4(),
            "workspace_key": ws,
            "response_id": uuid4(),
            "revision": 1,
            "vote": "down",
            "reason_code": "missing",
            "comment": "student note stays in the owner bundle",
        },
    )
    db.execute(
        queries.get("insert_job"),
        {
            "job_id": uuid4(),
            "kind": "artifact_generate",
            "scope_key": ws,
            "idempotency_key": str(uuid4()),
            "payload": Jsonb({"prompt": "private"}),
            "max_attempts": 3,
            "available_at": datetime.now(UTC),
        },
    )
    db.commit()
    client = api_client(db, collection)
    created = client.post("/api/privacy/exports")
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["downloadToken"]
    assert token
    inventory = body["manifest"]["inventory"]
    assert inventory["conversations"] == 1
    assert inventory["artifacts"] == 1
    assert inventory["academic_records"] == 1
    assert inventory["response_feedback"] == 1
    assert inventory["degree_audits"] == 0
    assert inventory["academic_imports"] == 0
    assert inventory["jobs"] == 1
    assert body["manifest"]["data"]["feedback"][0]["comment"] == "student note stays in the owner bundle"
    listed = client.get("/api/privacy/exports")
    assert listed.status_code == 200
    assert listed.json()
    assert all("downloadToken" not in item for item in listed.json())
    fetched = client.get(f"/api/privacy/exports/{body['exportId']}")
    assert fetched.status_code == 200
    assert "downloadToken" not in fetched.json()

    downloaded = client.get(
        f"/api/privacy/exports/{body['exportId']}/download",
        headers={"X-Export-Token": token},
    )
    assert downloaded.status_code == 200, downloaded.text
    payload = downloaded.json()
    assert payload["contentDigest"] == privacy_export_digest(payload["manifest"])
    assert payload["manifest"]["inventory"]["academic_records"] == 1
    assert "downloadToken" not in payload
    denied = client.get(
        f"/api/privacy/exports/{body['exportId']}/download",
        headers={"X-Export-Token": "not-the-owner-token"},
    )
    assert denied.status_code == 403
    # Non-ASCII token input is an invalid credential, not a server exception.
    with pytest.raises(HTTPException) as invalid_token:
        PrivacyService.download_export(db, UUID(body["exportId"]), ws, "\u00e9" * len(token))
    assert invalid_token.value.status_code == 403
    with pytest.raises(HTTPException) as other:
        PrivacyService.download_export(db, UUID(body["exportId"]), "other-owner", token)
    assert other.value.status_code == 404
    db.execute(
        queries.get("set_privacy_export_expiry"),
        {"export_id": UUID(body["exportId"]), "expires_at": datetime.now(UTC) - timedelta(minutes=1)},
    )
    db.commit()
    expired = client.get(
        f"/api/privacy/exports/{body['exportId']}/download",
        headers={"X-Export-Token": token},
    )
    assert expired.status_code == 410


def test_deletion_resume_does_not_restore_local_originals(db, tmp_path, monkeypatch):
    ws = "ws-delete-resume"
    user_id, _conv_id, _art_id = _setup_workspace_data(db, ws, tmp_path)
    spec = DocumentSpec(
        title="Private note",
        blocks=[ParagraphBlock(id="p1", text="Do not restore this file.")],
    )
    created = ArtifactStore(db, workspace_key=ws, data_dir=tmp_path).create(
        spec, original=b"private-bytes", filename="note.txt"
    )
    path = Path(created["original_path"])
    assert io_path(path).read_bytes() == b"private-bytes"
    db.execute(
        queries.get("get_or_create_academic_record"),
        {"record_id": uuid4(), "workspace_key": ws},
    )
    db.execute(
        queries.get("insert_response_feedback"),
        {
            "feedback_id": uuid4(),
            "workspace_key": ws,
            "response_id": uuid4(),
            "revision": 1,
            "vote": "up",
            "reason_code": "clear",
            "comment": None,
        },
    )
    db.commit()

    def interrupted(_conn, _workspace_key):
        raise RuntimeError("deletion interrupted")

    monkeypatch.setattr(PrivacyService, "_delete_owned_rows", interrupted)
    with pytest.raises(RuntimeError, match="deletion interrupted"):
        PrivacyService.delete_workspace(db, ws, user_id=user_id, data_dir=tmp_path)
    assert not io_path(path).exists()
    deletion = db.execute(
        queries.get("list_privacy_deletions"),
        {"workspace_key": ws, "limit": 1},
    ).fetchone()
    assert deletion["status"] == "failed"
    with pytest.raises(HTTPException) as blocked:
        ArtifactStore(db, workspace_key=ws, data_dir=tmp_path).create(
            spec, original=b"private-bytes", filename="note.txt"
        )
    assert blocked.value.status_code == 410
    assert not io_path(path).exists()

    monkeypatch.undo()
    resumed = PrivacyService.resume_deletion(
        db, ws, deletion["deletion_id"], data_dir=tmp_path
    )
    assert resumed["status"] == "completed"
    assert not io_path(path).exists()
    again = PrivacyService.resume_deletion(
        db, ws, deletion["deletion_id"], data_dir=tmp_path
    )
    assert again["status"] == "completed"
    assert not io_path(path).exists()
    inventory = dict(
        db.execute(queries.get("count_workspace_inventory"), {"workspace_key": ws}).fetchone()
    )
    assert inventory["conversations"] == 0
    assert inventory["artifacts"] == 0
    assert inventory["academic_records"] == 0
    assert inventory["response_feedback"] == 0
    assert inventory["degree_audits"] == 0
    with pytest.raises(HTTPException) as gone:
        PrivacyService.start_export(db, ws, user_id=user_id)
    assert gone.value.status_code == 410
