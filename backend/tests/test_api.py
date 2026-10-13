"""HTTP API tests. Fake provider/embedder; real Postgres. No models load."""



from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


def test_read_endpoints_and_basic_error_statuses(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    client = api_client(db, collection)
    name = collection_name(db, collection)
    assert client.get("/health").json() == {"status": "ok"}
    assert name in {item["name"] for item in client.get("/api/collections").json()}
    sources = client.get("/api/sources", params={"collection": name}).json()
    assert [s["name"] for s in sources] == ["a.txt"]
    assert sources[0]["status"] == "indexed" and sources[0]["chunkCount"] == 1
    assert client.get("/api/sources", params={"collection": "no-such"}).status_code == 404
    assert client.post("/api/query", json={"query": ""}).status_code == 422


def test_query_returns_grounded_answer(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    provider = FakeProvider(text="The answer is alpha [1].")
    client = api_client(db, collection, provider=provider)
    resp = client.post(
        "/api/query",
        json={"query": "alpha", "collectionId": collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "answer"
    assert body["answer"] == "The answer is alpha [1]."
    assert body["citations"][0]["sourceName"] == "a.txt"
    assert provider.calls == 1
    # The prompt fences the context as untrusted data.
    assert "untrusted" in body["trace"]["prompt"]["user"]
    assert body["trace"]["contextBlock"][0]["chunkId"]


def test_query_refusal_does_not_call_model(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    provider = FakeProvider()
    client = api_client(db, collection, provider=provider)
    resp = client.post(
        "/api/query",
        json={"query": "zzzzz qqqqq nothing", "collectionId": collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "refusal"
    assert provider.calls == 0
    gaps = client.get("/api/gaps").json()["gaps"]
    assert len(gaps) == 1 and gaps[0]["query"] == "zzzzz qqqqq nothing"
    assert gaps[0]["occurrenceCount"] == 1 and gaps[0]["sourceIds"] is None


def test_generation_gap_recurrence_and_provider_outage_are_distinct(db, collection, tmp_path):
    from app.llm.base import ProviderTimeoutError

    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    client = api_client(db, collection, provider=FakeProvider("Uncited assertion."))
    request = {"query": "alpha", "collectionId": collection_name(db, collection)}
    withheld = client.post("/api/query", json=request)
    assert withheld.status_code == 200 and withheld.json()["type"] == "refusal"
    gap = client.get("/api/gaps").json()["gaps"][0]
    assert gap["state"] == "open" and gap["occurrenceCount"] == 1

    client.app.state.provider = FakeProvider("Alpha [1].")
    answered = client.post("/api/query", json=request)
    assert answered.status_code == 200 and answered.json()["type"] == "answer"
    observed = client.get("/api/gaps").json()
    assert observed["gaps"][0]["gapId"] == gap["gapId"]
    assert observed["gaps"][0]["state"] == "open"
    assert observed["gaps"][0]["successfulRecurrenceCount"] == 1

    class OfflineProvider:
        def generate(self, _request):
            raise ProviderTimeoutError("provider unavailable", provider="fake")

    client.app.state.provider = OfflineProvider()
    assert client.post("/api/query", json=request).status_code == 503
    assert client.get("/api/gaps").json() == observed


def test_upload_source(db, collection):
    client = api_client(db, collection)
    resp = client.post(
        "/api/sources",
        files={"file": ("notes.txt", b"alpha beta gamma\n", "text/plain")},
        data={"collection": collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "notes.txt"
    assert body["status"] == "indexed"
    assert body["chunkCount"] == 1
    listed = client.get(
        "/api/sources", params={"collection": collection_name(db, collection)}
    ).json()
    assert any(s["name"] == "notes.txt" for s in listed)
