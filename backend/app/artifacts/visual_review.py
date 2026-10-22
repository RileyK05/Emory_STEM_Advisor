"""Render an HTML artifact and ask a vision model whether it actually looks right.

The renderer is deliberately a narrow seam. Tests inject a deterministic fake;
production enables the optional Playwright adapter. The screenshot is not
published or persisted: it is a private visual check for the generation loop.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from app.artifacts.models import HtmlArtifactSpec
from app.artifacts.storage import validate_spec
from app.config import Settings
from app.llm.base import GenerationRequest, GenerationResult, ImageMessage

MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
_SCREENSHOT_POLICY = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "script-src 'none'; font-src data:; connect-src 'none'; "
    "frame-src 'none'; object-src 'none'; base-uri 'none'"
)
_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "issues": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": ["blocking", "minor"]},
                    "summary": {"type": "string"},
                    "element_id": {"type": "string"},
                },
                "required": ["severity", "summary"],
            },
        },
        "revised_html": {"type": "string"},
    },
    "required": ["issues"],
}


class ScreenshotUnavailable(RuntimeError):
    """The configured screenshot renderer is not installed or not enabled."""


@dataclass(frozen=True)
class VisualReview:
    issues: tuple[dict, ...]
    revised_html: str | None
    screenshot_sha256: str
    screenshot_bytes: int


def screenshot_document(spec: HtmlArtifactSpec) -> str:
    """A network-dead, script-disabled document for the isolated renderer."""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{_SCREENSHOT_POLICY}\">"
        "<style>html,body{margin:0;padding:0;background:#fff;color:#111;"
        "font-family:system-ui,-apple-system,Segoe UI,sans-serif}"
        "img{max-width:100%;height:auto}table{border-collapse:collapse}"
        "th,td{border:1px solid #ddd;padding:4px 6px}</style></head><body>"
        f"{spec.html}</body></html>"
    )


def render_screenshot(spec: HtmlArtifactSpec, settings: Settings) -> bytes:
    """Render one artifact screenshot inside the configured sandbox.

    Production uses the separate worker endpoint; the Playwright branch is a
    local-development convenience only and never the deployed path.
    """
    if settings.screenshot_renderer == "disabled":
        raise ScreenshotUnavailable("screenshot rendering is disabled")
    if settings.screenshot_renderer == "sandbox":
        from app.sandbox.remote import RemoteSandboxConfig
        from app.sandbox.screenshot_remote import RemoteHtmlScreenshotRunner

        if not (settings.sandbox_remote_url and settings.sandbox_bearer_token):
            raise ScreenshotUnavailable("authenticated screenshot sandbox is not configured")
        config = RemoteSandboxConfig(
            base_url=settings.sandbox_remote_url,
            bearer_token=settings.sandbox_bearer_token,
            request_timeout_seconds=settings.screenshot_timeout_seconds + 30,
            max_timeout_seconds=settings.screenshot_timeout_seconds,
            max_response_bytes=32 * 1024 * 1024,
            max_output_bytes=settings.screenshot_max_bytes,
            max_input_bytes=4 * 1024 * 1024,
        )
        try:
            return RemoteHtmlScreenshotRunner(
                config, browser_image=settings.sandbox_screenshot_image,
                max_input_bytes=4 * 1024 * 1024,
                max_output_bytes=settings.screenshot_max_bytes,
            ).render(
                screenshot_document(spec),
                width=settings.screenshot_width,
                height=settings.screenshot_height,
                timeout_seconds=settings.screenshot_timeout_seconds,
            )
        except Exception as exc:
            raise ScreenshotUnavailable("sandbox screenshot rendering failed") from exc
    if settings.screenshot_renderer != "playwright":
        raise ScreenshotUnavailable("unsupported screenshot renderer")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ScreenshotUnavailable("Playwright is not installed") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": settings.screenshot_width,
                              "height": settings.screenshot_height},
                    java_script_enabled=False,
                    service_workers="block",
                )
                page = context.new_page()
                page.route("**/*", lambda route: route.abort())
                page.set_content(
                    screenshot_document(spec),
                    wait_until="load",
                    timeout=settings.screenshot_timeout_seconds * 1000,
                )
                image = page.screenshot(
                    type="png", full_page=True,
                    timeout=settings.screenshot_timeout_seconds * 1000,
                )
            finally:
                browser.close()
    except ScreenshotUnavailable:
        raise
    except Exception as exc:
        raise ScreenshotUnavailable("screenshot rendering failed") from exc

    if not image or len(image) > min(MAX_SCREENSHOT_BYTES, settings.screenshot_max_bytes):
        raise ScreenshotUnavailable("screenshot exceeds its configured byte limit")
    return image


def build_visual_review_request(
    spec: HtmlArtifactSpec, image: bytes, settings: Settings
) -> GenerationRequest:
    prompt = (
        "You are reviewing a rendered screenshot of an HTML artifact. "
        "Return only the requested JSON. List visual problems that a student "
        "would notice: broken layout, clipped or overlapping text, unreadable "
        "contrast, missing labels, empty charts, poor hierarchy, or content "
        "that does not match the intended title. If you can fix them without "
        "changing the facts or evidence, return the complete corrected HTML in "
        "revised_html. Never add scripts, iframes, forms, external URLs, or "
        "network resources. Keep the same content and source references. "
        "If the render is acceptable, return an empty issues list."
    )
    return GenerationRequest(
        [
            {"role": "system", "content": prompt},
            ImageMessage(
                content=f"Artifact title: {spec.title}\nReview this rendered screenshot.",
                mime_type="image/png",
                data=base64.b64encode(image).decode("ascii"),
            ),
        ],
        max_output_tokens=min(4096, settings.llm_max_output_tokens),
        temperature=0,
        response_schema=_REVIEW_SCHEMA,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def parse_visual_review(result: GenerationResult, image: bytes) -> VisualReview:
    if (not result.text.strip() or result.refusal
            or result.finish_reason not in {"stop", "end_turn", "completed"}):
        raise ValueError("visual review did not return a complete result")
    payload = json.loads(result.text)
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise TypeError("visual review result is invalid")
    issues = []
    for item in payload["issues"][:12]:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        severity = item.get("severity")
        if not isinstance(summary, str) or not summary.strip():
            continue
        issues.append({
            "severity": severity if severity in {"blocking", "minor"} else "minor",
            "summary": summary[:1000],
            "element_id": item.get("element_id") if isinstance(item.get("element_id"), str) else "",
        })
    revised = payload.get("revised_html")
    if not isinstance(revised, str) or not revised.strip():
        revised = None
    return VisualReview(
        issues=tuple(issues),
        revised_html=revised,
        screenshot_sha256=hashlib.sha256(image).hexdigest(),
        screenshot_bytes=len(image),
    )


def apply_visual_review(spec: HtmlArtifactSpec, review: VisualReview) -> HtmlArtifactSpec:
    """Return a validated revised spec while preserving evidence and provenance."""
    if not review.revised_html:
        return spec
    candidate = spec.model_copy(update={"html": review.revised_html})
    validated = validate_spec("html", candidate.model_dump(mode="json"))
    if not isinstance(validated, HtmlArtifactSpec):
        raise TypeError("visual review returned the wrong artifact kind")
    provenance = dict(validated.provenance)
    provenance.update({
        "visual_review": "revised",
        "visual_issue_count": len(review.issues),
        "screenshot_sha256": review.screenshot_sha256,
    })
    return validated.model_copy(update={"provenance": provenance})
