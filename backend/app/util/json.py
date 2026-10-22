"""JSON helpers for rows and wire payloads."""

import json
from datetime import date, datetime
from typing import Any

from psycopg.types.json import Jsonb


def iso(value: date | datetime | None) -> str | None:
    """ISO-8601 text for a timestamp, or None."""
    return value.isoformat() if value is not None else None


def loose_jsonb(data: Any) -> Jsonb:
    """A JSONB parameter that stringifies UUIDs, datetimes and Decimals."""
    return Jsonb(data, dumps=lambda obj: json.dumps(obj, default=str))
