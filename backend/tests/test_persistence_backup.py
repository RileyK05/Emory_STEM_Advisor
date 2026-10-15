"""Round-trip proof for the portable Postgres + original-file backup."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from app.artifacts.models import DocumentSpec, ParagraphBlock
from app.artifacts.storage import ArtifactStore
from app.db import queries
from app.db.connection import connect
from app.jobs.store import JobStore
from app.ops.backup import (
    FORMAT,
    BackupError,
    _load_and_verify,
    _pg_bin,
    _pg_env,
    create_backup,
    restore_backup,
)


def _create_database(dsn: str, name: str) -> None:
    env = _pg_env(dsn)
    maintenance_db = env["PGDATABASE"]
    env["PGDATABASE"] = maintenance_db
    subprocess.run(
        [str(_pg_bin("createdb")), "--maintenance-db", maintenance_db, name],
        env=env, check=True, capture_output=True,
    )


def _drop_database(dsn: str, name: str) -> None:
    env = _pg_env(dsn)
    subprocess.run(
        [str(_pg_bin("dropdb")), "--if-exists", name],
        env=env, check=True, capture_output=True,
    )


def test_pg_bin_prefers_the_client_whose_major_matches_the_server(monkeypatch, tmp_path):
    """EMO-018: pgserver's bundled pg_dump 16 must not beat a matching system 17."""
    from app.ops import backup

    bundled, system = tmp_path / "pg_dump-16", tmp_path / "pg_dump-17"
    majors = {bundled: 16, system: 17}
    monkeypatch.setattr(backup, "_pg_candidates", lambda _name: [bundled, system])
    monkeypatch.setattr(backup, "_tool_major", lambda path: majors[path])
    assert backup._pg_bin("pg_dump", server_major=17) == system
    assert backup._pg_bin("pg_dump", server_major=16) == bundled
    with pytest.raises(BackupError, match="major version must match"):
        backup._pg_bin("pg_dump", server_major=18)


@pytest.fixture
def short_dir():
    """A short scratch path: pytest's tmp_path plus the originals layout exceeds MAX_PATH on Windows."""
    path = Path(tempfile.mkdtemp(prefix="bk_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_backup_restores_citations_artifact_revisions_jobs_conversation_and_originals(
    db, collection, migrated_dsn, tmp_path, short_dir
):
    data_dir = short_dir / "src"
    target_data = short_dir / "dst"
    raw = b"Stable citation source bytes."
    digest = hashlib.sha256(raw).hexdigest()
    source_id, locator_id, chunk_id = uuid4(), uuid4(), uuid4()
    raw_path = data_dir / "raw" / str(collection) / digest
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(raw)
    db.execute(queries.get("upsert_source"), {
        "source_id": source_id, "collection_id": collection, "workspace_key": "workspace-a",
        "filename": "course.txt", "content_type": "text/plain", "file_hash": digest,
        "raw_path": str(raw_path), "origin_key": None,
    })
    second_collection = uuid4()
    db.execute(queries.get("insert_collection"), {
        "collection_id": second_collection, "name": f"shared-{second_collection}",
    })
    second_source_id = uuid4()
    db.execute(queries.get("upsert_source"), {
        "source_id": second_source_id, "collection_id": second_collection,
        "workspace_key": "workspace-a", "filename": "same-bytes.txt",
        "content_type": "text/plain", "file_hash": digest, "raw_path": str(raw_path),
        "origin_key": None,
    })
    db.execute(queries.get("insert_locator"), {
        "locator_id": locator_id, "source_id": source_id, "ordinal": 0,
        "locator_type": "line_range", "label": "Line 1", "start_char": 0, "end_char": 29,
    })
    db.execute(queries.get("insert_chunk"), {
        "chunk_id": chunk_id, "source_id": source_id, "locator_id": locator_id,
        "chunk_index": 0, "start_char": 0, "end_char": 29, "token_count": 5,
        "text": "Stable citation source bytes.",
    })
    db.commit()

    artifact_store = ArtifactStore(db, workspace_key="workspace-a", data_dir=data_dir)
    spec = DocumentSpec(title="Study guide", blocks=[ParagraphBlock(id="p1", text="First")])
    artifact = artifact_store.create(spec, original=b"original office bytes", filename="guide.docx")
    revised = DocumentSpec(title="Study guide", blocks=[ParagraphBlock(id="p1", text="Revised")])
    artifact_store.revise(artifact["artifact_id"], artifact["revision_id"], revised)

    jobs = JobStore(lambda: connect(migrated_dsn))
    job = jobs.create(
        kind="document", scope_key="workspace-a", idempotency_key="backup-test",
        payload={"prompt": "make guide"},
    )
    claimed = jobs.claim("backup-test-worker")
    jobs._finish_success(claimed, claimed.lease_token, {"artifactIds": [str(artifact["artifact_id"]) ]})

    # Unresolved provider usage and an unknown batch are durable evidence, not
    # live workers. Their accounting reservation must survive without settlement.
    operation_id = uuid4()
    db.execute(queries.get("create_operation_budget"), {
        "operation_id": operation_id, "workspace_key": "workspace-a",
        "active_budget_seconds": 60, "max_calls": 1, "total_token_budget": 1000,
        "per_call_output_cap": 100, "per_call_timeout_seconds": 10,
    })
    db.execute(queries.get("reserve_operation_budget_call"), {
        "operation_id": operation_id, "expected_call_count": 0,
        "expected_reserved_tokens": 0, "reservation_tokens": 50,
    })
    db.execute(queries.get("insert_operation_budget_call"), {
        "operation_id": operation_id, "attempt_no": 1,
        "prompt_reserved_tokens": 20, "output_reserved_tokens": 30,
    })
    db.execute(queries.get("keep_operation_call_reservation"), {
        "operation_id": operation_id, "attempt_no": 1, "status": "unknown",
    })
    batch_id = uuid4()
    db.execute(queries.get("insert_provider_batch"), {
        "batch_id": batch_id, "workspace_key": "workspace-a", "provider": "test-provider",
        "model_id": "test-model", "profile_id": None, "profile_revision": None,
        "request_fingerprint": "b" * 64, "operation_limits": Jsonb({"maxItems": 1}),
    })
    db.execute(queries.get("mark_provider_batch_unknown"), {
        "batch_id": batch_id, "workspace_key": "workspace-a", "failure_code": "unknown_outcome",
    })
    db.commit()

    # Expired running jobs have no live lease owner; preserve their state.
    fake_clock = [datetime.now(UTC) - timedelta(seconds=5)]
    expired_jobs = JobStore(lambda: connect(migrated_dsn), clock=lambda: fake_clock[0])
    expired = expired_jobs.create(
        kind="query", scope_key="workspace-a", idempotency_key="expired-lease", payload={}
    )
    expired = expired_jobs.claim("expired-backup-test", lease_seconds=1)
    assert expired is not None

    conversation_id, request_id = uuid4(), uuid4()
    db.execute(queries.get("create_conversation"), {
        "conversation_id": conversation_id, "workspace_key": "workspace-a", "title": "Biology",
    })
    db.execute(queries.get("create_conversation_request"), {
        "conversation_id": conversation_id, "request_id": request_id,
        "fingerprint": "f" * 64, "input_snapshot": Jsonb({"question": "What is cited?"}),
    })
    db.execute(queries.get("insert_conversation_turn"), {
        "conversation_id": conversation_id, "sequence": 1, "request_id": request_id,
        "role": "user", "text": "What is cited?", "status": "input", "evidence": Jsonb({}),
    })
    db.execute(queries.get("insert_conversation_turn"), {
        "conversation_id": conversation_id, "sequence": 2, "request_id": request_id,
        "role": "assistant", "text": "Stable citation source bytes.", "status": "answer",
        "evidence": Jsonb({"citations": [{"chunkId": str(chunk_id), "sourceId": str(source_id)}]}),
    })
    db.execute(queries.get("finish_conversation_request"), {
        "conversation_id": conversation_id, "request_id": request_id,
        "state": "completed", "result": Jsonb({"answer": "Stable citation source bytes."}),
    })
    db.execute(queries.get("advance_conversation"), {
        "conversation_id": conversation_id, "sequence": 2, "pending_request_id": None,
    })
    db.commit()

    archive = tmp_path / "advisor-backup.zip"
    manifest = create_backup(
        db, dsn=migrated_dsn, data_dir=data_dir, destination=archive,
        maintenance_confirmed=True,
    )
    assert len(manifest["files"]) == 2  # raw source plus imported artifact original
    raw_manifest = next(item for item in manifest["files"] if item["path"].startswith("raw/"))
    assert set(raw_manifest["source_ids"]) == {str(source_id), str(second_source_id)}
    assert archive.is_file()

    target_db_name = f"restore_{uuid4().hex}"
    _create_database(migrated_dsn, target_db_name)
    target_dsn = make_conninfo(migrated_dsn, dbname=target_db_name)
    target = connect(target_dsn)
    try:
        restore_backup(target, dsn=target_dsn, archive_path=archive, data_dir=target_data)
        restored_source = target.execute(
            queries.get("get_owned_source_for_reindex"),
            {"source_id": source_id, "workspace_key": "workspace-a"},
        ).fetchone()
        assert Path(restored_source["raw_path"]).read_bytes() == raw
        citation = target.execute(queries.get("cited_chunk_details"), {
            "chunk_id": chunk_id, "workspace_key": "workspace-a",
        }).fetchone()
        assert citation["source_id"] == source_id
        assert citation["source_collection_id"] == collection
        second_source = target.execute(queries.get("get_owned_source_for_reindex"), {
            "source_id": second_source_id, "workspace_key": "workspace-a",
        }).fetchone()
        assert Path(second_source["raw_path"]).read_bytes() == raw

        history = target.execute(queries.get("list_artifact_revisions"), {
            "artifact_id": artifact["artifact_id"], "workspace_key": "workspace-a",
        }).fetchall()
        assert len(history) == 2
        original_path = target.execute(queries.get("backup_get_artifact_original"), {
            "artifact_id": artifact["artifact_id"],
        }).fetchone()["original_path"]
        assert Path(original_path).read_bytes() == b"original office bytes"

        restored_job = target.execute(queries.get("get_job"), {"job_id": job.job_id}).fetchone()
        assert restored_job["status"] == "succeeded"
        events = target.execute(queries.get("list_job_events"), {
            "job_id": job.job_id, "after_event_id": 0, "limit": 20,
        }).fetchall()
        assert any(event["event_type"] == "succeeded" for event in events)
        expired_row = target.execute(queries.get("get_job"), {"job_id": expired.job_id}).fetchone()
        assert expired_row["status"] == "running"
        assert expired_row["lease_expires_at"] < datetime.now(UTC)
        usage = target.execute(queries.get("backup_operation_usage"), {
            "operation_id": operation_id, "attempt_no": 1,
        }).fetchone()
        assert usage == {"status": "unknown", "reserved_tokens": 50, "accounted_tokens": 0}
        batch = target.execute(queries.get("backup_provider_batch_state"), {
            "batch_id": batch_id,
        }).fetchone()
        assert batch == {"state": "unknown", "failure_code": "unknown_outcome"}
        turns = target.execute(queries.get("conversation_turns"), {
            "conversation_id": conversation_id, "after": 0, "limit": 10,
        }).fetchall()
        assert len(turns) == 2 and turns[1]["evidence"]["citations"][0]["chunkId"] == str(chunk_id)
        assert turns[0]["evidence"] == {}
    finally:
        target.close()
        _drop_database(migrated_dsn, target_db_name)


def test_backup_refuses_pending_decision_before_call_reservation(db, migrated_dsn, tmp_path):
    from app.operations.budget import OperationBudgetController, OperationLimits

    operation_id, profile_id, decision_id = uuid4(), uuid4(), uuid4()
    OperationBudgetController().ensure(db, operation_id, "backup-decision", OperationLimits())
    db.execute(queries.get("insert_connection_profile"), {
        "profile_id": profile_id, "name": f"backup-{profile_id}", "provider": "openai",
        "model_id": "test-model", "base_url": None, "account_id": None,
        "secret_ciphertext": None, "provider_options": Jsonb({}), "roles": ["decision"],
        "capabilities": ["decision"], "data_scopes": ["user_supplied_data"],
        "enabled": True, "priority": 1,
    })
    db.execute(queries.get("insert_decision_intent"), {
        "decision_id": decision_id, "workspace_key": "backup-decision",
        "operation_id": operation_id, "profile_id": profile_id, "profile_revision": 1,
        "provider": "openai", "model_id": "test-model", "request_fingerprint": "a" * 64,
        "request_json": Jsonb({"task": "test"}),
    })
    db.commit()
    assert not db.execute(queries.get("backup_ready")).fetchone()["ready"]
    with pytest.raises(BackupError, match="model requests remain pending"):
        create_backup(
            db, dsn=migrated_dsn, data_dir=tmp_path / "data",
            destination=tmp_path / "pending.zip", maintenance_confirmed=True,
        )
    db.rollback()
    db.execute(queries.get("abandon_decision"), {
        "decision_id": decision_id, "workspace_key": "backup-decision",
    })
    db.commit()
    assert db.execute(queries.get("backup_ready")).fetchone()["ready"]
    assert not (tmp_path / "pending.zip").exists()


def test_backup_refuses_live_jobs_and_existing_restore_targets(db, migrated_dsn, tmp_path):
    store = JobStore(lambda: connect(migrated_dsn))
    store.create(kind="query", scope_key="w", idempotency_key="pending", payload={})
    with pytest.raises(BackupError, match="jobs are queued or running"):
        create_backup(
            db, dsn=migrated_dsn, data_dir=tmp_path / "data", destination=tmp_path / "b.zip",
            maintenance_confirmed=True,
        )

    fresh_path = tmp_path / "new-target-data"
    with pytest.raises(BackupError, match="database is not empty"):
        restore_backup(
            db, dsn=migrated_dsn, archive_path=tmp_path / "missing.zip", data_dir=fresh_path
        )
    assert not fresh_path.exists()

    # A target data directory that exists is never overwritten, even if empty.
    existing = tmp_path / "existing-data"
    existing.mkdir()
    with pytest.raises(BackupError, match="must not already exist"):
        restore_backup(db, dsn=migrated_dsn, archive_path=tmp_path / "missing.zip", data_dir=existing)


def _write_test_archive(path: Path, *, file_path: str, payload: bytes, declared_hash: str) -> None:
    dump = b"not used by archive verification"
    manifest = {
        "format": FORMAT,
        "database_dump": {
            "path": "database.dump", "sha256": hashlib.sha256(dump).hexdigest(), "size": len(dump),
        },
        "files": [{
            "path": file_path.removeprefix("files/"),
            "sha256": declared_hash,
            "size": len(payload),
        }],
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("database.dump", dump)
        archive.writestr(file_path, payload)
        archive.writestr("manifest.json", json.dumps(manifest))


def test_restore_verifier_rejects_path_traversal_before_writing_outside_stage(tmp_path):
    archive = tmp_path / "traversal.zip"
    _write_test_archive(
        archive, file_path="files/raw/../../escape", payload=b"outside",
        declared_hash=hashlib.sha256(b"outside").hexdigest(),
    )
    stage = tmp_path / "stage"
    with pytest.raises(BackupError, match="invalid path"):
        _load_and_verify(archive, stage)
    assert not (tmp_path / "escape").exists()


def test_restore_verifier_rejects_corrupted_payload_checksum(tmp_path):
    archive = tmp_path / "bad-checksum.zip"
    _write_test_archive(
        archive, file_path="files/raw/collection/source", payload=b"tampered",
        declared_hash="0" * 64,
    )
    with pytest.raises(BackupError, match="failed checksum"):
        _load_and_verify(archive, tmp_path / "stage")
