"""Open a database connection for a script.

Thin wrapper over `app.db.connection.open_connection` (an explicit URL, then
`DATABASE_URL`, then an embedded `pgserver` instance; schema migrated on first
use). Kept as a module so existing scripts keep their import.
"""

from app.db.connection import open_connection

__all__ = ["open_connection"]
