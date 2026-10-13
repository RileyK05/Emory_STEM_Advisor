"""Every published route must say what it returns.

A route either declares a JSON response model (so its body is typed and
validated) or explicitly documents a non-JSON result (file media types,
redirects, 204). Adding a route that does neither fails here, instead of
shipping as an anonymous `Successful Response`.
"""

import pytest

from app.api import create_app
from app.config import load_settings
from tests.fakes import FakeEmbedder, FakeProvider

_SUCCESS = ("200", "201", "202", "204", "303")


@pytest.fixture(scope="module")
def operations():
    app = create_app(settings=load_settings(), allow_test_client=True,
                     embedder=FakeEmbedder(dim=8), provider=FakeProvider())
    paths = app.openapi()["paths"]
    return {(method.upper(), path): operation
            for path, methods in paths.items() for method, operation in methods.items()}


def _success(operation):
    for status in _SUCCESS:
        if status in operation["responses"]:
            return status, operation["responses"][status]
    raise AssertionError("no success response declared")


def test_every_route_declares_a_typed_body_or_a_documented_non_json_result(operations):
    undocumented = []
    for (method, path), operation in sorted(operations.items()):
        status, response = _success(operation)
        content = response.get("content", {})
        json_schema = content.get("application/json", {}).get("schema")
        typed_json = bool(json_schema)  # a response model produces a non-empty schema
        non_json = any(media != "application/json" for media in content)
        no_body = status in {"204", "303"} and response.get("description") != "Successful Response"
        if not (typed_json or non_json or no_body):
            undocumented.append(f"{method} {path}")
    assert not undocumented, "routes with no typed body or documented result: " + ", ".join(undocumented)


def test_file_routes_document_their_media_types(operations):
    download = _success(operations[("GET", "/api/artifacts/{artifact_id}/download")])[1]["content"]
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in download
    assert "text/markdown" in download
    pdf = _success(operations[("GET", "/api/artifacts/{artifact_id}/preview.pdf")])[1]["content"]
    assert "application/pdf" in pdf


def test_auth_redirects_and_logout_document_their_status(operations):
    assert _success(operations[("GET", "/api/auth/login")])[0] == "303"
    assert _success(operations[("GET", "/api/auth/callback")])[0] == "303"
    assert _success(operations[("POST", "/api/auth/logout")])[0] == "204"
