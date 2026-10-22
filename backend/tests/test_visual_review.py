"""Visual feedback for HTML artifacts: images in the provider seam and revision bounds."""

import json
from dataclasses import replace
from uuid import uuid4

from app.artifacts.models import ArtifactReference, HtmlArtifactSpec
from app.artifacts.visual_review import (
    apply_visual_review,
    build_visual_review_request,
    parse_visual_review,
    screenshot_document,
)
from app.config import load_settings
from app.llm.adapters import (
    _anthropic_messages,
    _chat_messages,
    _google_contents,
    _responses_input,
)
from app.llm.base import GenerationResult, ImageMessage, normalize_message


def _image():
    return ImageMessage(
        content="Review this render.",
        mime_type="image/png",
        data="iVBORw0KGgo=",
    )


def test_image_message_maps_to_each_provider_envelope():
    normalized = normalize_message(_image())
    assert normalized["role"] == "user"
    assert normalized["content"][1]["type"] == "image"
    assert normalized["content"][1]["mime_type"] == "image/png"

    responses = _responses_input([_image()])
    assert responses[0]["content"][1]["type"] == "input_image"
    assert responses[0]["content"][1]["image_url"].startswith("data:image/png;base64,")

    anthropic = _anthropic_messages([_image()])
    assert anthropic[0]["content"][1]["source"]["media_type"] == "image/png"

    google = _google_contents([_image()])
    assert google[0]["parts"][1]["inline_data"]["mime_type"] == "image/png"

    compatible = _chat_messages([_image()])
    assert compatible[0]["content"][1]["type"] == "image_url"


def test_visual_review_schema_and_revision_preserve_evidence():
    settings = replace(load_settings(), llm_max_output_tokens=512)
    spec = HtmlArtifactSpec(
        title="Course map",
        html="<h1>Course map</h1><p>CS 170</p>",
        evidence={"html": [ArtifactReference(
            chunk_id=uuid4(), source_id=uuid4(), source_name="catalog.pdf",
            locator_label="page 2", text_snapshot="CS 170 is required.",
        )]},
    )
    image = b"\x89PNG\r\n\x1a\nsmall"
    request = build_visual_review_request(spec, image, settings)
    assert request.response_schema["required"] == ["issues"]
    assert isinstance(request.messages[1], ImageMessage)
    assert screenshot_document(spec).startswith("<!doctype html>")
    assert "script-src 'none'" in screenshot_document(spec)

    review = parse_visual_review(
        GenerationResult(
            text=json.dumps({
                "issues": [{"severity": "blocking", "summary": "Text overlaps the chart."}],
                "revised_html": "<h1>Course map</h1><p>CS 170</p><div>Fixed</div>",
            }),
            finish_reason="stop",
        ),
        image,
    )
    revised = apply_visual_review(spec, review)
    assert revised.html.endswith("Fixed</div>")
    assert revised.evidence == spec.evidence
    assert revised.provenance["visual_review"] == "revised"
    assert revised.provenance["visual_issue_count"] == 1
