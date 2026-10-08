"""Named-query loader. Every SQL statement lives in a .sql file, not Python.

A block starts with a line ``-- name: <name>`` and runs to the next such line.
Placeholders use psycopg's ``%(name)s`` style. A literal percent sign in SQL
must be written ``%%``, including inside comments.
"""

import re
from functools import lru_cache
from pathlib import Path

_QUERIES_DIR = Path(__file__).parent / "queries"
_NAME_RE = re.compile(r"^\s*--\s*name:\s*(\S+)\s*$")


class QueryLoadError(RuntimeError):
    """A malformed query file or a duplicate block name."""


def _parse(text: str, source: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if name is None:
            return
        if name in blocks:
            raise QueryLoadError(f"duplicate query name {name!r} in {source}")
        blocks[name] = "\n".join(lines).strip()

    for line in text.splitlines():
        match = _NAME_RE.match(line)
        if match:
            flush()
            name = match.group(1)
            lines = []
            continue
        if name is None:
            stripped = line.strip()
            if stripped and not stripped.startswith("--"):
                raise QueryLoadError(
                    f"SQL before the first '-- name:' block in {source}: {stripped!r}"
                )
            continue
        lines.append(line)
    flush()
    return blocks


def load_queries(directory: Path = _QUERIES_DIR) -> dict[str, str]:
    """Load every ``*.sql`` block in ``directory`` (sorted for determinism)."""
    blocks: dict[str, str] = {}
    if not directory.is_dir():
        return blocks
    for path in sorted(directory.glob("*.sql")):
        for name, sql in _parse(path.read_text(encoding="utf-8"), path.name).items():
            if name in blocks:
                raise QueryLoadError(f"duplicate query name {name!r} in {path.name}")
            blocks[name] = sql
    return blocks


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    return load_queries()


def get(name: str) -> str:
    try:
        return _load()[name]
    except KeyError:
        raise KeyError(f"unknown query name: {name!r}") from None
