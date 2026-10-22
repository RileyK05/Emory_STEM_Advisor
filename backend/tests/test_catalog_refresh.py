"""Catalog editions pin the current sources and topic build; cancel never retires them."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.admin.catalog_refresh import CatalogRefreshService
from app.db import queries
from tests.test_degree_audit import _create_user


def _indexed_source(db, collection, *, workspace_key=None):
    source_id = uuid4()
    db.execute(
        """
        INSERT INTO sources (
            source_id, collection_id, filename, content_type, file_hash, raw_path,
            status, index_revision, is_current, workspace_key
        ) VALUES (%s, %s, 'atlas.jsonl', 'application/x-course-atlas+jsonl', %s,
                  '/tmp/atlas.jsonl', 'indexed', 1, true, %s)
        """,
        (source_id, collection, uuid4().hex * 2, workspace_key),
    )
    return source_id


def test_refresh_publishes_editions_and_cancel_keeps_the_current_one(db, collection):
    admin = _create_user(db, role="admin")
    with pytest.raises(HTTPException) as empty:
        CatalogRefreshService.start(db, collection, "2026", True, admin)
    assert empty.value.status_code == 409
    db.rollback()

    catalog = _indexed_source(db, collection)
    _indexed_source(db, collection, workspace_key="student-ws")  # never part of an edition
    build_id = uuid4()
    db.execute(
        """
        INSERT INTO sim_builds (
            build_id, collection_id, model, config_version, is_current, content_hash, graph_version
        ) VALUES (%s, %s, 'test', 1, true, %s, 2)
        """,
        (build_id, collection, "a" * 64),
    )
    db.commit()

    # The first refresh publishes revision 1 against no prior edition.
    first = CatalogRefreshService.start(db, collection, "2026", True, admin)
    assert first["current_edition"] is None
    assert first["staged_edition"]["source_ids"] == [catalog]
    assert first["staged_edition"]["topic_build_id"] == build_id
    published = CatalogRefreshService.publish(db, first["batch_id"], 0, admin)
    assert published["current_edition"]["revision"] == 1
    db.commit()

    newer = _indexed_source(db, collection)
    db.commit()
    second = CatalogRefreshService.start(db, collection, "2027", True, admin)
    assert set(second["staged_edition"]["source_ids"]) == {catalog, newer}
    assert CatalogRefreshService.current(db, collection, admin)["staging"]["batch_id"] == (
        second["batch_id"])
    cancelled = CatalogRefreshService.cancel(db, second["batch_id"], admin)
    assert cancelled["status"] == "cancelled"
    assert cancelled["current_edition"]["revision"] == 1
    assert cancelled["current_edition"]["is_current"] is True
    assert db.execute(queries.get("get_current_build"),
                      {"collection_id": collection}).fetchone()["build_id"] == build_id

    third = CatalogRefreshService.start(db, collection, "2027", True, admin)
    db.commit()
    with pytest.raises(HTTPException) as busy:
        CatalogRefreshService.start(db, collection, "2028", True, admin)
    assert busy.value.status_code == 409
    db.rollback()
    with pytest.raises(HTTPException) as stale:
        CatalogRefreshService.publish(db, third["batch_id"], 0, admin)
    assert stale.value.status_code == 409
    db.rollback()
    final = CatalogRefreshService.publish(db, third["batch_id"], 1, admin)
    assert final["current_edition"]["revision"] == 3  # revision 2 stays the cancelled edition
    assert CatalogRefreshService.current(db, collection, admin)["staging"] is None
