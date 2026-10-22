"""Capture browser-copied Spring 2027 Course Atlas DOM into immutable local files.

Run with ``python -m scripts.manual.capture_course_atlas`` from ``backend/``.
The helper serves only a local form; it does not visit the Atlas or write to the
database. Paste the browser-observed DOM into the form's HTML field.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "data" / "atlas" / "spring-2027-capture"
MAX_REQUEST_BYTES = 20 * 1024 * 1024
SOURCE_HOST = "atlas.emory.edu"
_REQUIRED = {"kind", "source_url", "term", "career", "html", "observed_at"}


class CaptureError(ValueError):
    """Invalid or unsafe browser capture input."""


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_record(value: object) -> dict:
    if not isinstance(value, dict):
        raise CaptureError("each capture must be a JSON object")
    missing = _REQUIRED - value.keys()
    if missing:
        raise CaptureError(f"missing required fields: {', '.join(sorted(missing))}")
    if value["kind"] not in {"index", "detail"}:
        raise CaptureError("kind must be 'index' or 'detail'")
    for name in ("source_url", "term", "career", "html", "observed_at"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise CaptureError(f"{name} must be a non-empty string")
    if value["term"] != "Spring 2027":
        raise CaptureError("term must be 'Spring 2027'")
    if any(key.casefold() in {"host", "externalorigin"} for key in value):
        raise CaptureError("Host and externalOrigin are not capture metadata")

    source = urlsplit(value["source_url"])
    if (source.scheme != "https" or source.hostname != SOURCE_HOST
            or source.username is not None or source.password is not None
            or source.port not in {None, 443}):
        raise CaptureError("source_url must be an HTTPS URL on atlas.emory.edu")
    try:
        observed = datetime.fromisoformat(value["observed_at"])
    except ValueError as exc:
        raise CaptureError("observed_at must be an ISO 8601 UTC timestamp") from exc
    if observed.tzinfo is None or observed.utcoffset() is None or observed.utcoffset().total_seconds() != 0:
        raise CaptureError("observed_at must be an ISO 8601 UTC timestamp")
    return value


def _save_records(out_dir: Path, records: object) -> int:
    if not isinstance(records, list) or not records:
        raise CaptureError("request must contain a non-empty JSON array")
    validated = [_validate_record(record) for record in records]
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for record in validated:
        payload = _canonical_bytes(record)
        digest = hashlib.sha256(payload).hexdigest()
        path = out_dir / f"{digest}.json"
        fd, temp_name = tempfile.mkstemp(dir=out_dir, prefix=".capture-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_name, path)
            except FileExistsError:
                if path.read_bytes() != payload + b"\n":
                    raise CaptureError("capture hash collision; refusing to replace existing file")
            saved += 1
        finally:
            Path(temp_name).unlink(missing_ok=True)
    return saved


def _page(*, status: str = "", error: str = "", count: int = 0) -> bytes:
    status_block = (
        f'<p role="status">{html.escape(status)}</p>' if status else ""
    )
    error_block = f'<p role="alert">{html.escape(error)}</p>' if error else ""
    body = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Course Atlas capture</title>
<body><main><h1>Spring 2027 Course Atlas capture</h1>
<p>Copy the browser-observed DOM and save a local evidence capture. This page does not access the Atlas.</p>
{status_block}{error_block}
<div id="feedback" aria-live="polite"></div>
<form method="post" action="/capture" id="capture-form">
<label for="kind">Capture kind</label>
<select id="kind" name="kind"><option>index</option><option>detail</option></select>
<label for="source_url">Observed Atlas URL</label>
<input id="source_url" name="source_url" type="url" required value="https://atlas.emory.edu/">
<label for="career">Career</label><input id="career" name="career" required>
<label for="payload">Raw DOM HTML</label>
<textarea id="payload" name="payload" rows="24" required spellcheck="false"></textarea>
<label for="metadata">Extra DOM metadata (optional JSON object)</label>
<textarea id="metadata" name="metadata" rows="4" spellcheck="false">{{}}</textarea>
<button type="submit">Save capture</button></form>
<form id="batch-form">
<label for="batch-payload">Capture batch JSON</label>
<textarea id="batch-payload" name="batch-payload" rows="16" spellcheck="false"></textarea>
<button type="submit">Save batch</button></form>
<div id="fragment-form">
<label for="fragment">Batch fragment</label>
<textarea id="fragment" rows="5" spellcheck="false"></textarea>
<button type="button" id="append-fragment">Append fragment</button>
<button type="button" id="save-assembled">Save assembled batch</button>
</div><p id="fragment-status">Buffered 0 characters.</p>
<p>Saved {count} capture(s) in this submission.</p></main>
<script>
var fragments = '';
document.getElementById('append-fragment').addEventListener('click', () => {{
  const field = document.getElementById('fragment');
  fragments += field.value;
  field.value = '';
  document.getElementById('fragment-status').textContent = `Buffered ${{fragments.length}} characters.`;
}});
document.getElementById('save-assembled').addEventListener('click', async () => {{
  try {{
    const records = JSON.parse(fragments);
    if (!Array.isArray(records) || !records.length) throw new Error('Expected a nonempty array.');
    const response = await fetch('/capture', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(records)}});
    const page = await response.text();
    if (!response.ok) throw new Error(new DOMParser().parseFromString(page, 'text/html')
      .querySelector('[role="alert"]')?.textContent || 'Save failed.');
    document.open(); document.write(page); document.close();
  }} catch (error) {{ document.getElementById('feedback').textContent = String(error); }}
}});
async function submitRecords(records) {{
  const response = await fetch('/capture', {{method: 'POST',
    headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(records)}});
  const page = await response.text();
  if (response.ok) {{ document.open(); document.write(page); document.close(); }}
  else {{
    const message = new DOMParser().parseFromString(page, 'text/html').querySelector('[role="alert"]')?.textContent;
    document.getElementById('feedback').textContent = message || 'Capture was rejected; the form is unchanged.';
  }}
}}
document.getElementById('capture-form').addEventListener('submit', async (event) => {{
  event.preventDefault();
  const form = event.currentTarget;
  let extra;
  try {{ extra = JSON.parse(form.elements.metadata.value || '{{}}'); }}
  catch {{ document.getElementById('feedback').textContent = 'Extra DOM metadata must be valid JSON.'; return; }}
  if (!extra || Array.isArray(extra) || typeof extra !== 'object') {{
    document.getElementById('feedback').textContent = 'Extra DOM metadata must be a JSON object.'; return;
  }}
  const record = {{...extra, kind: form.elements.kind.value,
    source_url: form.elements.source_url.value, term: 'Spring 2027',
    career: form.elements.career.value, html: form.elements.payload.value,
    observed_at: new Date().toISOString()}};
  await submitRecords([record]);
}});
document.getElementById('batch-form').addEventListener('submit', async (event) => {{
  event.preventDefault();
  let records;
  try {{ records = JSON.parse(document.getElementById('batch-payload').value); }}
  catch {{ document.getElementById('feedback').textContent = 'Capture batch must be valid JSON.'; return; }}
  if (!Array.isArray(records) || records.length === 0) {{
    document.getElementById('feedback').textContent = 'Capture batch JSON must be a non-empty array.';
    return;
  }}
  await submitRecords(records);
}});
</script></body></html>"""
    return body.encode("utf-8")


def make_handler(out_dir: Path, port_ref: list[int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalCapture/1.0"

        def _send(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _local_request(self) -> bool:
            host = self.headers.get("Host", "")
            try:
                parsed = urlsplit(f"http://{host}")
                port = parsed.port
            except ValueError:
                return False
            if parsed.hostname not in {"127.0.0.1", "localhost"} or port != port_ref[0]:
                return False
            origin = self.headers.get("Origin")
            if origin:
                try:
                    origin_url = urlsplit(origin)
                    origin_port = origin_url.port
                except ValueError:
                    return False
                if (origin_url.scheme != "http" or origin_url.hostname not in {"127.0.0.1", "localhost"}
                        or origin_port != port_ref[0]):
                    return False
            return True

        def do_GET(self) -> None:
            if not self._local_request():
                self._send(403, _page(error="Local requests only."))
                return
            if urlsplit(self.path).path != "/":
                self._send(404, _page(error="Not found."))
                return
            self._send(200, _page())

        def do_POST(self) -> None:
            if not self._local_request():
                self._send(403, _page(error="Local requests only."))
                return
            if self.path != "/capture":
                self._send(404, _page(error="Not found."))
                return
            length_text = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,9}", length_text):
                self._send(411, _page(error="A valid Content-Length is required."))
                return
            length = int(length_text)
            if length > MAX_REQUEST_BYTES:
                self._send(413, _page(error="Request exceeds the 20 MiB limit."))
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, _page(error="POST application/json captures only."))
                return
            try:
                value = json.loads(self.rfile.read(length))
                count = _save_records(out_dir, value)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._send(400, _page(error=str(exc)))
                return
            self._send(200, _page(status=f"Saved {count} capture(s).", count=count))

        def log_message(self, format: str, *args: object) -> None:
            print(f"capture-server: {format % args}", file=sys.stderr)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"capture directory (default: {DEFAULT_OUT})")
    parser.add_argument("--port", type=int, default=0,
                        help="loopback port (default: choose an available port)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    out_dir = args.out.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    port_ref = [args.port]
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(out_dir, port_ref))
    port_ref[0] = server.server_address[1]
    print(f"Capture form: http://127.0.0.1:{port_ref[0]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping capture server.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
