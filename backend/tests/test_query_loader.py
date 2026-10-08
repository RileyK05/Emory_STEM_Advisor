"""Named-query loader tests. No database needed."""

import pytest

from app.db import queries
from app.db.queries import QueryLoadError, load_queries


def test_parses_named_blocks(tmp_path):
    (tmp_path / "a.sql").write_text(
        "-- header comment, ignored\n"
        "-- name: first\n"
        "SELECT 1;\n"
        "-- name: second\n"
        "SELECT 2;\n",
        encoding="utf-8",
    )
    blocks = load_queries(tmp_path)
    assert blocks == {"first": "SELECT 1;", "second": "SELECT 2;"}


def test_duplicate_name_within_a_file_rejected(tmp_path):
    (tmp_path / "a.sql").write_text(
        "-- name: dup\nSELECT 1;\n-- name: dup\nSELECT 2;\n", encoding="utf-8"
    )
    with pytest.raises(QueryLoadError, match="duplicate query name 'dup'"):
        load_queries(tmp_path)


def test_duplicate_name_across_files_rejected(tmp_path):
    (tmp_path / "a.sql").write_text("-- name: dup\nSELECT 1;\n", encoding="utf-8")
    (tmp_path / "b.sql").write_text("-- name: dup\nSELECT 2;\n", encoding="utf-8")
    with pytest.raises(QueryLoadError, match="duplicate query name 'dup'"):
        load_queries(tmp_path)


def test_sql_before_first_named_block_rejected(tmp_path):
    (tmp_path / "a.sql").write_text(
        "SELECT 1;\n-- name: only\nSELECT 2;\n", encoding="utf-8"
    )
    with pytest.raises(QueryLoadError, match="SQL before the first"):
        load_queries(tmp_path)


def test_leading_comments_and_blank_lines_allowed(tmp_path):
    (tmp_path / "a.sql").write_text(
        "-- a header\n\n-- another comment\n-- name: only\nSELECT 1;\n",
        encoding="utf-8",
    )
    assert load_queries(tmp_path) == {"only": "SELECT 1;"}


def test_get_unknown_name_raises():
    with pytest.raises(KeyError):
        queries.get("definitely_not_a_real_query")


def test_shipped_queries_load():
    blocks = queries.load_queries()
    assert "insert_collection" in blocks
    assert "get_collection_by_name" in blocks
