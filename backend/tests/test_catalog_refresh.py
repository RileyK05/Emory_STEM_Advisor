"""Cancelling a catalog refresh leaves the published edition and topic build usable."""

from uuid import uuid4

from app.admin.catalog_refresh import CatalogRefreshService
from app.db import queries
from tests.test_degree_audit import _create_user


def test_cancel_refresh_keeps_prior_edition_and_topic_build(db, collection):
    admin = _create_user(db, role="admin")
    source_id = uuid4()
    build_id = uuid4()
    edition_id = uuid4()
    content_hash = "a" * 64
    db.execute(
        """
        INSERT INTO sources (
            source_id, collection_id, filename, content_type, file_hash, raw_path,
            status, index_revision, is_current
        ) VALUES (%s, %s, 'atlas.jsonl', 'application/x-course-atlas+jsonl', %s, '/tmp/atlas.jsonl',
                  'indexed', 1, true)
        """,
        (source_id, collection, content_hash),
    )
    db.execute(
        """
        INSERT INTO sim_builds (
            build_id, collection_id, model, config_version, is_current, content_hash, graph_version
        ) VALUES (%s, %s, 'test', 1, true, %s, 2)
        """,
        (build_id, collection, content_hash),
    )
    db.execute(
        queries.get("insert_catalog_edition"),
        {
            "edition_id": edition_id,
            "collection_id": collection,
            "revision": 1,
            "edition_key": "2026",
            "source_ids": [source_id],
            "topic_build_id": build_id,
            "is_current": True,
        },
    )
    db.commit()

    started = CatalogRefreshService.start(db, collection, "2027", True, admin)
    assert started["status"] == "staging"
    assert started["current_edition"]["edition_id"] == edition_id
    assert started["staged_edition"]["is_current"] is False
    assert started["staged_edition"]["topic_build_id"] == build_id

    cancelled = CatalogRefreshService.cancel(db, started["batch_id"], admin)
    assert cancelled["status"] == "cancelled"
    assert cancelled["current_edition"]["edition_id"] == edition_id
    assert cancelled["current_edition"]["is_current"] is True
    assert cancelled["staged_edition"]["is_current"] is False

    current_build = db.execute(
        queries.get("get_current_build"),
        {"collection_id": collection},
    ).fetchone()
    assert current_build["build_id"] == build_id
    source = db.execute(
        "SELECT is_current FROM sources WHERE source_id = %s",
        (source_id,),
    ).fetchone()
    assert source["is_current"] is True
