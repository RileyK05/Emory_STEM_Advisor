"""Serving databases must be migrated and have an upload target."""


from app.db import queries
from app.db.bootstrap import prepare_database


def test_bootstrap_creates_default_collection_once(db):
    prepare_database(db, "serving-default")
    first = db.execute(queries.get("get_collection_by_name"), {"name": "serving-default"}).fetchone()
    db.commit()
    prepare_database(db, "serving-default")
    second = db.execute(queries.get("get_collection_by_name"), {"name": "serving-default"}).fetchone()
    assert first["collection_id"] == second["collection_id"]
