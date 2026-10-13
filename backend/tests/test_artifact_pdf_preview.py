"""Artifact PDF route tests use a renderer double, not Docker isolation proof."""


from tests.helpers import api_client


class PdfRenderer:
    def __init__(self):
        self.docx_inputs = []
        self.xlsx_inputs = []

    def recalculate_xlsx(self, data: bytes, *, timeout_seconds: int) -> bytes:
        self.xlsx_inputs.append((data, timeout_seconds))
        return data

    def render_docx_pdf(self, data: bytes, *, timeout_seconds: int = 60) -> bytes:
        self.docx_inputs.append((data, timeout_seconds))
        assert data.startswith(b"PK\x03\x04")
        return b"%PDF-1.7\nmock document preview"

    def render_xlsx_pdf(self, data: bytes, *, timeout_seconds: int = 60) -> bytes:
        self.xlsx_inputs.append((data, timeout_seconds))
        assert data.startswith(b"PK\x03\x04")
        return b"%PDF-1.7\nmock workbook preview"


def test_document_pdf_preview_uses_exact_revision_and_isolated_runner(db, collection):
    client = api_client(db, collection)
    renderer = PdfRenderer()
    client.app.state.office_runner = renderer
    created = client.post("/api/artifacts", json={
        "kind": "document",
        "spec": {"kind": "document", "title": "Preview", "blocks": [
            {"id": "p", "type": "paragraph", "text": "A document"},
        ]},
    }).json()
    response = client.get(
        f"/api/artifacts/{created['artifactId']}/preview.pdf",
        params={"revisionId": created["revisionId"]},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-artifact-revision"] == created["revisionId"]
    assert response.headers["x-office-fidelity"] == "unverified"
    assert len(renderer.docx_inputs) == 1
    assert renderer.xlsx_inputs == []


def test_pdf_preview_fails_closed_without_office_runner(db, collection):
    client = api_client(db, collection)
    created = client.post("/api/artifacts", json={
        "kind": "document",
        "spec": {"kind": "document", "title": "Preview", "blocks": []},
    }).json()
    response = client.get(f"/api/artifacts/{created['artifactId']}/preview.pdf")
    assert response.status_code == 503
    assert response.json()["code"] == "office_unavailable"


def test_formula_workbook_pdf_preview_is_withheld_without_verified_cache(db, collection):
    client = api_client(db, collection)
    renderer = PdfRenderer()
    client.app.state.office_runner = renderer
    created = client.post("/api/artifacts", json={
        "kind": "workbook",
        "spec": {"kind": "workbook", "title": "Formula", "sheets": [{
            "id": "s1", "name": "Sheet1", "cells": [
                {"id": "a1", "row": 1, "column": 1, "value": 2},
                {"id": "a2", "row": 2, "column": 1, "formula": "=SUM(A1:A1)"},
            ],
        }]},
    }).json()
    response = client.get(f"/api/artifacts/{created['artifactId']}/preview.pdf")
    assert response.status_code == 409
    assert response.json()["code"] == "calculation_unverified"
    assert len(renderer.xlsx_inputs) == 1  # recalculation was attempted
