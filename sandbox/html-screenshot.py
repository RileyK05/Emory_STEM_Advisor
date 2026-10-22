#!/usr/bin/python3
"""Fixed screenshot command used only inside the disposable browser sandbox."""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

MAX_HTML_BYTES = 4 * 1024 * 1024


def main() -> int:
    if len(sys.argv) != 3:
        return 64
    try:
        width, height = int(sys.argv[1]), int(sys.argv[2])
    except ValueError:
        return 64
    if not 320 <= width <= 3840 or not 240 <= height <= 2160:
        return 64
    source = Path("/inputs/input.html")
    try:
        html = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return 65
    if not html or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        return 65

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport={"width": width, "height": height},
                java_script_enabled=False,
                service_workers="block",
            )
            page = context.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="load", timeout=30_000)
            page.screenshot(path="/output/screenshot.png", type="png", full_page=True)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
