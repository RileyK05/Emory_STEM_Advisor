import uuid

import pytest

from tests.helpers import api_client


@pytest.fixture
def test_source(db, collection):
    source_id = uuid.uuid4()
    db.execute(
        """
        INSERT INTO sources (source_id, collection_id, filename, content_type, file_hash, raw_path, status, index_revision, is_current)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 1, true)
        """,
        (source_id, collection, "handbook.md", "text/markdown", "a" * 64, "/tmp/handbook.md", "indexed"),
    )
    db.execute(
        """
        INSERT INTO source_provenance (source_id, source_category, classification_origin, content_hash, metadata_revision, lifecycle_status)
        VALUES (%s, 'institutional_policy', 'verified_import', %s, 1, 'active')
        """,
        (source_id, "a" * 64),
    )
    db.commit()
    return source_id


def test_invalidation_consumption_is_idempotent_and_keeps_history(db, collection, test_source):
    """A leased consumer marks the saved audit stale once and leaves history readable."""
    from app.admin.invalidation import consume_source_invalidations
    from app.gaps.store import GapStore

    program_id = uuid.uuid4()
    release_id = uuid.uuid4()
    record_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    gap_id = uuid.uuid4()
    db.execute(
        """
        INSERT INTO programs (
            program_id, collection_id, title, kind, catalog, source_url,
            first_page, last_page, parser_version, source_id, source_index_revision,
            source_sha256, origin_key, binding_status
        ) VALUES (
            %s, %s, 'History Program', 'major', 'emory_college', 'https://college.emory.edu/hist',
            1, 2, '1', %s, 1, %s, 'origin-hist', 'bound'
        )
        """,
        (program_id, collection, test_source, "a" * 64),
    )
    db.execute(
        """
        INSERT INTO program_rule_releases (
            release_id, program_id, collection_id, source_id, source_index_revision,
            source_sha256, release_version, status, rules_tree, rules_digest
        ) VALUES (
            %s, %s, %s, %s, 1, %s, 1, 'published', %s::jsonb, %s
        )
        """,
        (release_id, program_id, collection, test_source, "a" * 64, '{"nodeType": "all_of"}', "c" * 64),
    )
    db.execute(
        "INSERT INTO student_academic_records (record_id, workspace_key) VALUES (%s, 'development')",
        (record_id,),
    )
    db.execute(
        """
        INSERT INTO degree_audits (
            audit_id, workspace_key, program_id, rule_release_id, record_id, record_revision,
            rules_digest, overall_status
        ) VALUES (%s, 'development', %s, %s, %s, 1, %s, 'outstanding')
        """,
        (audit_id, program_id, release_id, record_id, "c" * 64),
    )
    db.execute(
        """
        INSERT INTO retrieval_gaps (
            gap_id, workspace_key, collection_id, query, normalized_query, query_key,
            state, resolution_source_ids, resolution_note, resolved_by, resolved_at
        ) VALUES (
            %s, 'development', %s, 'old requirement', 'old requirement', %s,
            'resolved', %s, 'reviewed', 'advisor', now() - interval '5 minutes'
        )
        """,
        (gap_id, collection, "d" * 64, [test_source]),
    )
    outbox_id = uuid.uuid4()
    db.execute(
        """
        INSERT INTO source_invalidation_outbox (outbox_id, source_id, event_type, payload)
        VALUES (%s, %s, 'source_provenance_updated', %s::jsonb)
        """,
        (outbox_id, test_source, '{"old_revision": 1, "new_revision": 2}'),
    )
    db.commit()

    # The gap is ineligible as soon as the event exists, before a worker consumes it.
    listed = GapStore(db, "development").list(collection_id=collection)
    resolved = next(gap for gap in listed["gaps"] if gap["gapId"] == gap_id)
    assert resolved["resolution"]["evidenceState"] == "ineligible"

    assert consume_source_invalidations(db, "worker-a") == 1
    db.commit()
    stale = db.execute(
        "SELECT is_stale FROM degree_audits WHERE audit_id = %s", (audit_id,),
    ).fetchone()
    assert stale["is_stale"] is True
    consumed = db.execute(
        "SELECT published, consumed_at, attempts FROM source_invalidation_outbox WHERE outbox_id = %s",
        (outbox_id,),
    ).fetchone()
    assert consumed["published"] is True
    assert consumed["consumed_at"] is not None
    assert consumed["attempts"] == 1
    assert consume_source_invalidations(db, "worker-b") == 0
    db.commit()
    again = db.execute(
        "SELECT attempts FROM source_invalidation_outbox WHERE outbox_id = %s", (outbox_id,),
    ).fetchone()
    assert again["attempts"] == 1

    # The saved approval history and the audit row remain addressable.
    detail = api_client(db, collection).get(f"/api/admin/sources/{test_source}/review")
    assert detail.status_code == 200
    saved = db.execute(
        "SELECT audit_id, overall_status FROM degree_audits WHERE audit_id = %s", (audit_id,),
    ).fetchone()
    assert saved["overall_status"] == "outstanding"


def test_historical_source_revision_evidence_survives_reindex(db, collection, tmp_path):
    """A replaced index stays readable at the revision that published it."""
    from app.ingest.pipeline import ingest_file
    from tests.fakes import FakeEmbedder, whitespace_tokens
    from tests.helpers import MODEL, lenient_config

    text = "Historical clause alpha stays addressable.\n"
    path = tmp_path / "clause.md"
    path.write_text(text, encoding="utf-8")
    source_id = ingest_file(
        db, collection_id=collection, path=path, content_type="text/markdown",
        count_tokens=whitespace_tokens, embedder=FakeEmbedder(8), embed_model=MODEL,
        config=lenient_config(),
    )
    ingest_file(
        db, collection_id=collection, path=path, content_type="text/markdown",
        count_tokens=whitespace_tokens, embedder=FakeEmbedder(8), embed_model=MODEL,
        config=lenient_config(), force=True,
    )
    db.execute("UPDATE chunks SET text = 'MUTATED LIVE' WHERE source_id = %s", (source_id,))
    db.commit()

    client = api_client(db, collection)
    first = client.get(f"/api/admin/sources/{source_id}/evidence", params={"index_revision": 1})
    second = client.get(f"/api/admin/sources/{source_id}/evidence", params={"index_revision": 2})
    missing = client.get(f"/api/admin/sources/{source_id}/evidence", params={"index_revision": 9})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert missing.status_code == 200, missing.text
    assert text.strip() in {row["text"] for row in first.json()["passages"]}
    assert text.strip() in {row["text"] for row in second.json()["passages"]}
    assert first.json()["passages"][0]["textDigest"] != ""
    assert missing.json()["passages"] == []
    assert "MUTATED LIVE" not in {row["text"] for row in first.json()["passages"] + second.json()["passages"]}


def test_get_source_review_detail_and_impact(db, collection, test_source):
    client = api_client(db, collection)
    # Bind a program to test_source
    prog_id = uuid.uuid4()
    db.execute(
        """
        INSERT INTO programs (program_id, collection_id, title, kind, catalog, source_url, first_page, last_page, parser_version, source_id, source_index_revision, binding_status)
        VALUES (%s, %s, 'Computer Science BS', 'major', 'emory_college', 'https://college.emory.edu/cs', 1, 2, 'v1', %s, 1, 'bound')
        """,
        (prog_id, collection, test_source),
    )
    db.commit()

    resp = client.get(f"/api/admin/sources/{test_source}/review")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source"]["sourceId"] == str(test_source)
    assert data["source"]["sourceCategory"] == "institutional_policy"
    assert data["dependencies"]["programCount"] == 1
    assert data["dependencies"]["programs"][0]["title"] == "Computer Science BS"

    impact_resp = client.get(f"/api/admin/sources/{test_source}/impact")
    assert impact_resp.status_code == 200, impact_resp.text
    impact_data = impact_resp.json()
    assert impact_data["programCount"] == 1
    assert impact_data["programs"][0]["title"] == "Computer Science BS"


def test_proposal_lifecycle_and_validation(db, collection, test_source):
    client = api_client(db, collection)
    # Invalid category
    resp = client.post(
        f"/api/admin/sources/{test_source}/proposals",
        json={
            "expectedMetadataRevision": 1,
            "sourceCategory": "invalid_category",
            "title": "New Title",
        },
    )
    assert resp.status_code == 422

    # Metadata revision mismatch
    resp = client.post(
        f"/api/admin/sources/{test_source}/proposals",
        json={
            "expectedMetadataRevision": 99,
            "sourceCategory": "institutional_catalog",
            "title": "New Title",
        },
    )
    assert resp.status_code == 409

    # Valid proposal
    resp = client.post(
        f"/api/admin/sources/{test_source}/proposals",
        json={
            "expectedMetadataRevision": 1,
            "sourceCategory": "institutional_catalog",
            "title": "2026-2027 College Catalog",
            "publisher": "Emory College",
            "sourceUrl": "https://college.emory.edu/catalog",
            "applicabilityStatus": "reviewed_default",
            "documentRoles": ["official_rules", "degree_requirements"],
            "applicability": {"cohort_year": ["2026", "2027"]},
            "lifecycleStatus": "active",
            "reviewNotes": "Annual catalog update",
        },
    )
    assert resp.status_code == 201, resp.text
    proposal = resp.json()
    assert proposal["status"] == "pending"
    assert proposal["title"] == "2026-2027 College Catalog"
    proposal_id = proposal["proposalId"]

    # Approve proposal
    approve_resp = client.post(
        f"/api/admin/sources/{test_source}/proposals/{proposal_id}/approve"
    )
    assert approve_resp.status_code == 200, approve_resp.text
    approved = approve_resp.json()
    assert approved["status"] == "approved"

    # Verify provenance was updated and revision bumped to 2
    detail_resp = client.get(f"/api/admin/sources/{test_source}/review")
    detail = detail_resp.json()
    assert detail["source"]["metadataRevision"] == 2
    assert detail["source"]["classificationOrigin"] == "verified_curator"
    assert detail["source"]["title"] == "2026-2027 College Catalog"

    # Verify invalidation outbox recorded the update
    outbox_row = db.execute(
        "SELECT * FROM source_invalidation_outbox WHERE source_id = %s",
        (test_source,),
    ).fetchone()
    assert outbox_row is not None
    assert outbox_row["event_type"] == "source_provenance_updated"

    # Attempting to re-approve already approved proposal fails with 409
    dup_approve = client.post(
        f"/api/admin/sources/{test_source}/proposals/{proposal_id}/approve"
    )
    assert dup_approve.status_code == 409


def test_proposal_rejection(db, collection, test_source):
    client = api_client(db, collection)
    resp = client.post(
        f"/api/admin/sources/{test_source}/proposals",
        json={
            "expectedMetadataRevision": 1,
            "sourceCategory": "career_reference",
            "title": "Career Guide",
        },
    )
    assert resp.status_code == 201
    proposal_id = resp.json()["proposalId"]

    reject_resp = client.post(
        f"/api/admin/sources/{test_source}/proposals/{proposal_id}/reject",
        json={"reason": "Insufficient institutional citation"},
    )
    assert reject_resp.status_code == 200
    rejected = reject_resp.json()
    assert rejected["status"] == "rejected"
    assert rejected["rejectionReason"] == "Insufficient institutional citation"


def test_source_lifecycle_state_change(db, collection, test_source):
    client = api_client(db, collection)
    # Deprecate source
    dep_resp = client.post(
        f"/api/admin/sources/{test_source}/lifecycle",
        json={
            "action": "deprecate",
            "expectedMetadataRevision": 1,
            "notes": "Superseded by 2027 edition",
        },
    )
    assert dep_resp.status_code == 200, dep_resp.text
    data = dep_resp.json()
    assert data["lifecycleStatus"] == "deprecated"
    assert data["metadataRevision"] == 2

    # Verify is_current was set to false
    source_row = db.execute(
        "SELECT is_current FROM sources WHERE source_id = %s",
        (test_source,),
    ).fetchone()
    assert source_row["is_current"] is False

    # Re-activate source
    act_resp = client.post(
        f"/api/admin/sources/{test_source}/lifecycle",
        json={
            "action": "activate",
            "expectedMetadataRevision": 2,
            "notes": "Restored active status",
        },
    )
    assert act_resp.status_code == 200
    data2 = act_resp.json()
    assert data2["lifecycleStatus"] == "active"
    assert data2["metadataRevision"] == 3

    source_row2 = db.execute(
        "SELECT is_current FROM sources WHERE source_id = %s",
        (test_source,),
    ).fetchone()
    assert source_row2["is_current"] is True
