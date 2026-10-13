"""Initialize a serving database without loading models or copying prior schemas."""

from uuid import NAMESPACE_URL, uuid5

from app.db import queries
from app.db.migrate import apply_migrations


def prepare_database(conn, default_collection: str) -> None:
    apply_migrations(conn)
    conn.execute(queries.get("insert_collection"), {
        "collection_id": uuid5(NAMESPACE_URL, "emory-stem-advisor:collection:" + default_collection),
        "name": default_collection,
    })
    conn.commit()
