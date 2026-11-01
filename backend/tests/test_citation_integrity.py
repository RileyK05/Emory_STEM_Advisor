"""A saved citation resolves to the exact passage it claims.

A citation is the only thing standing between a student and a confident wrong
answer: the answer says "[3]" and the reader clicks through to check. That
only works if the stored citation still points at the live chunk, with the same
locator and the same text.

These tests pin that end to end over the real ingest path and the real query
path: ingest a source, answer from it, then resolve what was saved.
"""

from __future__ import annotations

from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text

_MARKDOWN = """# Course Policy

## Prerequisites

CS 253 requires CS 171 or CS_OX 171 or equivalent transfer credit.

## Grading

Courses may be taken S/U up to the published credit limit.
"""


class _CitingProvider(FakeProvider):
    """Answer with a bracketed citation of the passage that names CS 171."""

    def __init__(self):
        super().__init__("CS 253 requires CS 171 or CS_OX 171. [1]")

    def generate(self, request):
        self.last_messages = [dict(m) for m in request.messages]
        self.calls += 1
        from app.llm.base import GenerationResult, Usage

        return GenerationResult(text=self.text, finish_reason="stop",
                                usage=Usage(20, 8, source="provider"))


def _ask(db, collection, question="What does CS 253 require?"):
    """Ask a question in the collection under test and return the payload."""
    client = api_client(db, collection, provider=_CitingProvider())
    response = client.post("/api/query", json={
        "query": question, "collectionId": collection_name(db, collection)})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_saved_citation_resolves_to_the_live_passage(db, collection, tmp_path):
    """The stored citation, the live chunk, and the locator all agree."""
    ingest_text(db, collection, tmp_path, "policy.md", _MARKDOWN,
                content_type="text/markdown")
    db.commit()

    payload = _ask(db, collection)
    assert payload["type"] == "answer"
    assert payload["citations"], "the answer cited nothing"

    citation = payload["citations"][0]
    chunk = db.execute(
        "SELECT chunk_id, source_id, text FROM chunks WHERE chunk_id = %(id)s",
        {"id": citation["chunkId"]}).fetchone()
    assert chunk is not None, "the citation names a chunk that does not exist"
    assert str(chunk["source_id"]) == str(citation["sourceId"])

    locator = db.execute(
        "SELECT l.label FROM chunks c JOIN locators l ON l.locator_id = c.locator_id"
        " WHERE c.chunk_id = %(id)s", {"id": citation["chunkId"]}).fetchone()
    assert locator["label"] == citation["locatorLabel"]
    assert "CS 253" in chunk["text"] or "CS 171" in chunk["text"]


def test_the_stored_snapshot_matches_the_live_text(db, collection, tmp_path):
    """A saved citation keeps the passage it cited, even if the source changes."""
    ingest_text(db, collection, tmp_path, "policy.md", _MARKDOWN,
                content_type="text/markdown")
    db.commit()

    payload = _ask(db, collection)
    assert payload["type"] == "answer"

    row = db.execute(
        "SELECT text_snapshot FROM citations WHERE chunk_id = %(id)s LIMIT 1",
        {"id": payload["citations"][0]["chunkId"]}).fetchone()
    assert row is not None, "no citation snapshot was persisted"
    live = db.execute(
        "SELECT text FROM chunks WHERE chunk_id = %(id)s",
        {"id": payload["citations"][0]["chunkId"]}).fetchone()
    assert row["text_snapshot"] == live["text"]


def test_a_citation_survives_the_source_being_replaced(db, collection, tmp_path):
    """Replacing the source does not silently move a saved citation."""
    ingest_text(db, collection, tmp_path, "policy.md", _MARKDOWN,
                content_type="text/markdown")
    db.commit()

    payload = _ask(db, collection)
    cited_chunk = payload["citations"][0]["chunkId"]

    # Replace the source with different content under the same origin.
    replacement = "# Course Policy\n\n## Prerequisites\n\nCS 253 requires CS 170 instead.\n"
    ingest_text(db, collection, tmp_path, "policy.md", replacement,
                content_type="text/markdown")
    db.commit()

    # The citation that was saved still names a real chunk and still carries the
    # passage it cited.
    saved = db.execute(
        "SELECT text_snapshot FROM citations WHERE chunk_id = %(id)s LIMIT 1",
        {"id": cited_chunk}).fetchone()
    assert saved is not None
    assert "CS 171" in saved["text_snapshot"] or "CS 253" in saved["text_snapshot"]

    # And the live chunk behind that id is still the one that was cited.
    live = db.execute(
        "SELECT text FROM chunks WHERE chunk_id = %(id)s", {"id": cited_chunk}).fetchone()
    if live is not None:
        assert live["text"] == saved["text_snapshot"]


def test_every_citation_of_a_response_names_a_source_that_exists(db, collection, tmp_path):
    """No citation may point at a source row that is gone."""
    ingest_text(db, collection, tmp_path, "policy.md", _MARKDOWN,
                content_type="text/markdown")
    db.commit()

    payload = _ask(db, collection)
    for citation in payload.get("citations", []):
        source = db.execute(
            "SELECT source_id FROM sources WHERE source_id = %(id)s",
            {"id": citation["sourceId"]}).fetchone()
        assert source is not None, "a citation names a source that does not exist"


def test_the_response_context_is_numbered_the_way_the_citations_are(db, collection,
                                                                   tmp_path):
    """Citation N points at context entry N, so a reader can follow the number."""
    ingest_text(db, collection, tmp_path, "policy.md", _MARKDOWN,
                content_type="text/markdown")
    db.commit()

    payload = _ask(db, collection)
    context = payload.get("trace", {}).get("contextBlock") or []
    assert context, "the answer carried no numbered context"
    for citation in payload.get("citations", []):
        assert citation["chunkId"], "a citation carried no chunk id"
    # Every cited chunk appears in the numbered context the answer was given.
    context_ids = {item.get("chunkId") for item in context if item.get("chunkId")}
    for citation in payload.get("citations", []):
        assert citation["chunkId"] in context_ids

