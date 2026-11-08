"""Tests for blob storage, local storage, dual-read transition, and replicas (Item 26)."""

import pytest

from app.storage.base import (
    StorageIntegrityError,
    StorageNotFoundError,
    compute_sha256,
)
from app.storage.blob import BlobStorageAdapter
from app.storage.dual import DualReadStorageAdapter
from app.storage.local import LocalStorageAdapter


def test_local_storage_adapter_crud_and_traversal(tmp_path):
    storage = LocalStorageAdapter(tmp_path)
    key = "artifacts/originals/test_doc.txt"
    content = b"Emory STEM Advising degree checklist data"

    # 1. Put
    obj = storage.put(key, content, content_type="text/plain")
    assert obj.key == key
    assert obj.size_bytes == len(content)
    assert obj.sha256 == compute_sha256(content)

    # 2. Exists and Get
    assert storage.exists(key) is True
    assert storage.get(key) == content

    # 3. List
    listed = storage.list("artifacts/")
    assert len(listed) == 1
    assert listed[0].key == key

    # 4. Path traversal attempt raises ValueError
    with pytest.raises(ValueError):
        storage.get("../../../windows/system32/cmd.exe")

    # 5. Delete
    assert storage.delete(key) is True
    assert storage.exists(key) is False
    with pytest.raises(StorageNotFoundError):
        storage.get(key)


def test_local_storage_detects_tampering_and_keeps_content_type(tmp_path):
    storage = LocalStorageAdapter(tmp_path)
    key = "transcripts/original.pdf"
    storage.put(key, b"private bytes", content_type="application/pdf")
    assert storage.list("transcripts/")[0].content_type == "application/pdf"
    (tmp_path / key).write_bytes(b"tampered")
    with pytest.raises(StorageIntegrityError):
        storage.get(key)


def test_replica_upload_and_read_matching_bytes():
    # Simulated shared cloud blob container between two server replicas
    shared_cloud_container = {}

    replica_a = BlobStorageAdapter(container_name="prod-advising", shared_backend=shared_cloud_container)
    replica_b = BlobStorageAdapter(container_name="prod-advising", shared_backend=shared_cloud_container)

    key = "uploads/2026/transcript_math.pdf"
    content = b"%PDF-1.4 simulated academic transcript binary bytes"

    # Replica A uploads
    meta = replica_a.put(key, content, content_type="application/pdf")
    assert meta.sha256 == compute_sha256(content)

    # Replica B reads matching bytes
    assert replica_b.exists(key) is True
    retrieved = replica_b.get(key)
    assert retrieved == content
    assert compute_sha256(retrieved) == meta.sha256


def test_dual_read_transition_and_automatic_backfill(tmp_path):
    primary_blob = BlobStorageAdapter()
    secondary_local = LocalStorageAdapter(tmp_path)

    dual = DualReadStorageAdapter(primary=primary_blob, secondary=secondary_local)

    key = "legacy/catalog_requirements.json"
    legacy_content = b'{"major": "Applied Mathematics", "credits": 60}'

    # File exists only in legacy secondary storage
    secondary_local.put(key, legacy_content)
    assert primary_blob.exists(key) is False

    # Dual-read get retrieves content and backfills into primary
    data = dual.get(key)
    assert data == legacy_content

    # Primary now has the file backfilled
    assert primary_blob.exists(key) is True
    assert primary_blob.get(key) == legacy_content


def test_separate_blob_adapters_do_not_share_durable_bytes():
    first = BlobStorageAdapter()
    first.put("student/original.bin", b"private")
    second = BlobStorageAdapter()
    assert second.exists("student/original.bin") is False
    assert first.connection_string is None


def test_storage_factory_refuses_the_in_memory_blob_adapter(tmp_path):
    from types import SimpleNamespace

    from app.storage.factory import build_storage

    for backend in ("blob", "dual"):
        with pytest.raises(RuntimeError, match="remote blob storage is not configured"):
            build_storage(SimpleNamespace(data_dir=str(tmp_path), storage_backend=backend), data_dir=tmp_path)


@pytest.mark.parametrize("backend", ["local", "blob"])
def test_artifact_originals_and_raw_uploads_store_backend_neutral_keys(
    db, collection, tmp_path, backend,
):
    from app.artifacts.models import DocumentSpec, ParagraphBlock
    from app.artifacts.storage import ArtifactStore
    from app.db import queries
    from app.ingest.pipeline import ingest_file
    from app.storage.keys import artifact_original_key, raw_source_key
    from tests.fakes import FakeEmbedder, whitespace_tokens
    from tests.helpers import lenient_config

    shared = {}
    storage = (
        LocalStorageAdapter(tmp_path)
        if backend == "local"
        else BlobStorageAdapter(shared_backend=shared)
    )

    from app.util.localpath import io_path

    def stored(key: str) -> bytes:
        return io_path(tmp_path / key).read_bytes() if backend == "local" else storage.get(key)

    store = ArtifactStore(
        db, workspace_key="development", data_dir=tmp_path, storage=storage
    )
    original = b"original office bytes"
    artifact = store.create(
        DocumentSpec(title="Plan", blocks=[ParagraphBlock(id="p1", text="Fall")]),
        original=original,
        filename="plan.docx",
    )
    digest = compute_sha256(original)
    artifact_key = artifact_original_key("development", digest)
    assert artifact["original_storage_key"] == artifact_key
    assert artifact["original_path"] == artifact_key
    assert stored(artifact_key) == original
    row = db.execute(
        queries.get("get_storage_object"), {"storage_key": artifact_key}
    ).fetchone()
    assert row["sha256"] == digest and row["backend"] == backend

    path = tmp_path / "notes.txt"
    path.write_bytes(b"alpha beta gamma\n")
    source_id = ingest_file(
        db, collection_id=collection, path=path, content_type="text/plain",
        count_tokens=whitespace_tokens, embedder=FakeEmbedder(dim=8),
        embed_model="test-embed", config=lenient_config(),
        workspace_key="development", storage=storage,
    )
    raw_key = db.execute(
        "SELECT raw_path FROM sources WHERE source_id = %s", (source_id,)
    ).fetchone()["raw_path"]
    assert raw_key == raw_source_key(str(collection), compute_sha256(b"alpha beta gamma\n"),
                                     "development")
    assert stored(raw_key) == b"alpha beta gamma\n"


def test_blob_storage_checksum_integrity_error():
    blob = BlobStorageAdapter()
    key = "tamper_test.bin"
    blob.put(key, b"legitimate content")

    # Tamper with the underlying stored bytes
    blob._store[key]["data"] = b"tampered content"

    with pytest.raises(StorageIntegrityError):
        blob.get(key)

