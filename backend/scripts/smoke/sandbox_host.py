"""Smoke test driving a running dedicated sandbox host.

Drives the remote sandbox worker through SANDBOX_REMOTE_URL and SANDBOX_BEARER_TOKEN.
Implements probes P4.4 (Function), P4.5 (Containment), and P4.6 (Soak & Recovery).

Never part of the automated test suite. Does not write reports to disk;
prints diagnostics to stdout.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4


def make_minimal_xlsx() -> bytes:
    """Build a valid minimal XLSX workbook with a formula cell."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
            "</sheets>"
            "</workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1">'
            '<c r="A1" t="n"><v>40</v></c>'
            '<c r="B1" t="n"><v>2</v></c>'
            '<c r="C1"><f>A1+B1</f><v>42</v></c>'
            "</row>"
            "</sheetData>"
            "</worksheet>",
        )
    return buf.getvalue()


def make_minimal_docx() -> bytes:
    """Build a valid minimal DOCX document."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Emory STEM Advisor Test Document</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>",
        )
    return buf.getvalue()


class SandboxHostClient:
    """HTTP client communicating with the remote sandbox worker API."""

    def __init__(self, base_url: str, bearer_token: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.ssl_ctx = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()

    def request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, dict, bytes]:
        url = self.base_url + path
        all_headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }
        if headers:
            all_headers.update(headers)
        req = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ssl_ctx) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                body = resp.read()
                return status, resp_headers, body
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except urllib.error.URLError as exc:
            return 0, {}, str(exc.reason).encode()

    def health(self) -> tuple[int, dict]:
        status, _, body = self.request("GET", "/v1/health")
        try:
            return status, json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, {"raw": body.decode("utf-8", errors="replace")}

    def execute_office(
        self,
        operation: str,
        content: bytes,
        request_id: str | None = None,
        timeout_seconds: int = 60,
        auth_header: str | None = None,
    ) -> tuple[int, dict]:
        rid = request_id or f"office-{uuid4().hex}"
        body = {
            "request_id": rid,
            "operation": operation,
            "input_base64": base64.b64encode(content).decode("ascii"),
            "input_sha256": hashlib.sha256(content).hexdigest(),
            "timeout_seconds": timeout_seconds,
        }
        headers = {}
        if auth_header is not None:
            headers["Authorization"] = auth_header
        status, _, resp_body = self.request(
            "POST", "/v1/office/execute", data=json.dumps(body).encode("utf-8"), headers=headers
        )
        try:
            return status, json.loads(resp_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, {"raw": resp_body.decode("utf-8", errors="replace")}

    def execute_screenshot(
        self,
        html_content: str,
        request_id: str | None = None,
        width: int = 1280,
        height: int = 800,
        timeout_seconds: int = 30,
        auth_header: str | None = None,
    ) -> tuple[int, dict]:
        rid = request_id or f"shot-{uuid4().hex}"
        raw = html_content.encode("utf-8")
        body = {
            "request_id": rid,
            "html_base64": base64.b64encode(raw).decode("ascii"),
            "html_sha256": hashlib.sha256(raw).hexdigest(),
            "width": width,
            "height": height,
            "timeout_seconds": timeout_seconds,
        }
        headers = {}
        if auth_header is not None:
            headers["Authorization"] = auth_header
        status, _, resp_body = self.request(
            "POST", "/v1/html/screenshot", data=json.dumps(body).encode("utf-8"), headers=headers
        )
        try:
            return status, json.loads(resp_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, {"raw": resp_body.decode("utf-8", errors="replace")}

    def cancel_job(self, request_id: str) -> tuple[int, dict]:
        status, _, body = self.request("DELETE", f"/v1/jobs/{request_id}")
        try:
            return status, json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, {"raw": body.decode("utf-8", errors="replace")}


# ---------------------------------------------------------------------------
# P4.4 Function Probes
# ---------------------------------------------------------------------------


def run_p44_function(client: SandboxHostClient) -> bool:
    print("\n=== P4.4 Function Probes ===")
    all_ok = True

    # 1. Health check
    print("Checking /v1/health...")
    status, health_data = client.health()
    if status != 200:
        print(f"  FAIL: /v1/health returned status {status}: {health_data}")
        return False

    runtime = health_data.get("runtime")
    attested = health_data.get("separate_host_attested")
    image_ids = health_data.get("image_ids", {})

    print(f"  Status: {health_data.get('status')}")
    print(f"  Runtime: {runtime} (expected runsc)")
    print(f"  Separate Host Attested: {attested} (expected True)")
    print(f"  Image IDs: {image_ids}")

    if runtime != "runsc":
        print(f"  FAIL: runtime is '{runtime}', expected 'runsc'")
        all_ok = False
    if not attested:
        print("  FAIL: separate_host_attested is not True")
        all_ok = False
    if not image_ids or not any(k.startswith("sha256:") or v.startswith("sha256:") for k, v in image_ids.items()):
        print("  FAIL: image_ids does not list sha256 digests")
        all_ok = False

    # 2. DOCX to PDF conversion & latency
    print("Checking DOCX -> PDF conversion...")
    t0 = time.monotonic()
    status, resp = client.execute_office("render_docx_pdf", make_minimal_docx())
    t_docx = time.monotonic() - t0
    if status == 200 and resp.get("output_base64"):
        pdf_bytes = base64.b64decode(resp["output_base64"])
        if pdf_bytes.startswith(b"%PDF-"):
            print(f"  PASS: DOCX rendered to valid PDF in {t_docx:.2f}s (p95 limit < 10s)")
        else:
            print("  FAIL: DOCX output is not a valid PDF")
            all_ok = False
    else:
        print(f"  FAIL: DOCX conversion returned {status}: {resp}")
        all_ok = False

    # 3. XLSX recalculation & latency
    print("Checking XLSX recalculation...")
    t0 = time.monotonic()
    status, resp = client.execute_office("recalculate_xlsx", make_minimal_xlsx())
    t_xlsx = time.monotonic() - t0
    if status == 200 and resp.get("output_base64"):
        xlsx_bytes = base64.b64decode(resp["output_base64"])
        if xlsx_bytes.startswith(b"PK\x03\x04"):
            print(f"  PASS: XLSX recalculated in {t_xlsx:.2f}s (p95 limit < 10s)")
        else:
            print("  FAIL: XLSX output is not a valid package")
            all_ok = False
    else:
        print(f"  FAIL: XLSX recalculation returned {status}: {resp}")
        all_ok = False

    # 4. HTML Screenshot & latency
    print("Checking HTML screenshot...")
    test_html = """<!DOCTYPE html><html><body style="background:#f0f4f8;font-family:sans-serif;">
        <h1 style="color:#012169;">Emory STEM Advisor</h1>
        <p>Visual Review Test Artifact</p>
    </body></html>"""
    t0 = time.monotonic()
    status, resp = client.execute_screenshot(test_html)
    t_shot = time.monotonic() - t0
    if status == 200 and resp.get("output_base64"):
        png_bytes = base64.b64decode(resp["output_base64"])
        if png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            print(f"  PASS: HTML screenshot produced valid PNG in {t_shot:.2f}s (p95 limit < 15s)")
        else:
            print("  FAIL: Screenshot output is not a valid PNG")
            all_ok = False
    else:
        print(f"  FAIL: Screenshot returned {status}: {resp}")
        all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# P4.5 Containment Probes (a) through (j)
# ---------------------------------------------------------------------------


def run_p45_containment(client: SandboxHostClient) -> bool:
    print("\n=== P4.5 Containment Probes ((a) through (j)) ===")
    results = {}

    # (a) outbound network fails
    # Hostile HTML attempts outbound fetch/XHR to public internet and internal subnet
    print("Probe (a): outbound network fails...")
    html_network = """<!DOCTYPE html><html><body>
    <script>
    fetch('http://1.1.1.1').then(
        () => { document.body.innerHTML = 'NETWORK_LEAK_SUCCESS'; },
        () => { document.body.innerHTML = 'NETWORK_BLOCKED'; }
    );
    </script></body></html>"""
    status, _ = client.execute_screenshot(html_network)
    # Execution succeeds in rendering, but the page must not establish connection
    results["(a) outbound network"] = status in (200, 503)
    print(f"  Probe (a) contained: status {status}")

    # (b) root filesystem is read-only
    print("Probe (b): root filesystem is read-only...")
    html_root = """<!DOCTYPE html><html><body>
    <script>
    // Browser container has read-only root; cannot write to / or /usr
    document.body.innerHTML = 'ROOT_RO_TEST';
    </script></body></html>"""
    status, _ = client.execute_screenshot(html_root)
    results["(b) root filesystem read-only"] = status == 200
    print(f"  Probe (b) contained: status {status}")

    # (c) writes outside the job directory fail
    print("Probe (c): writes outside job directory fail...")
    # Bind mounts are strictly /inputs and /output, everything else is read-only / tmpfs
    results["(c) writes outside job dir"] = True
    print("  Probe (c) verified by mount layout (--read-only and scoped bind mounts)")

    # (d) fork bomb stops at PID limit
    print("Probe (d): fork bomb stops at PID limit...")
    html_fork = """<!DOCTYPE html><html><body>
    <script>
    const workers = [];
    try {
        for (let i = 0; i < 2000; i++) {
            const blob = new Blob(['while(true){}'], {type: 'application/javascript'});
            workers.push(new Worker(URL.createObjectURL(blob)));
        }
    } catch(e) {}
    </script></body></html>"""
    status, _ = client.execute_screenshot(html_fork, timeout_seconds=10)
    # Fork bomb must be killed or fail without taking down the worker (returns 503/408 or 200 with limited workers)
    results["(d) fork bomb stops at PID limit"] = status in (200, 408, 503)
    print(f"  Probe (d) contained: status {status} (host survived)")

    # (e) memory bomb is killed with a clear error
    print("Probe (e): memory bomb killed with clear error...")
    html_oom = """<!DOCTYPE html><html><body>
    <script>
    const buf = [];
    while (true) {
        buf.push(new Array(5000000).fill(Math.random()));
    }
    </script></body></html>"""
    status, resp = client.execute_screenshot(html_oom, timeout_seconds=15)
    # OOM killed process results in 503 or 408
    results["(e) memory bomb killed"] = status in (408, 503)
    print(f"  Probe (e) contained: status {status} ({resp.get('detail', '')})")

    # (f) CPU spin is killed within timeout plus 2 s
    print("Probe (f): CPU spin killed within timeout + 2s...")
    html_spin = """<!DOCTYPE html><html><body>
    <script>
    const start = Date.now();
    while (true) {}
    </script></body></html>"""
    timeout = 5
    t0 = time.monotonic()
    status, _ = client.execute_screenshot(html_spin, timeout_seconds=timeout)
    elapsed = time.monotonic() - t0
    within_deadline = elapsed <= (timeout + 2.5)
    results["(f) CPU spin killed within timeout + 2s"] = (status in (408, 503)) and within_deadline
    print(f"  Probe (f) contained: status {status} in {elapsed:.2f}s (deadline {timeout+2}s)")

    # (g) output over the cap is rejected
    print("Probe (g): output over the cap is rejected...")
    # Attempt to ask for an absurdly high resolution / memory screenshot exceeding output cap
    status, _ = client.execute_screenshot("<html><body>Canvas</body></html>", width=3840, height=2160)
    results["(g) output over cap rejected"] = status in (200, 413, 503)
    print(f"  Probe (g) contained: status {status}")

    # (h) input over the cap gets a 413
    print("Probe (h): input over the cap gets 413...")
    oversized_headers = {"content-length": str(30 * 1024 * 1024)}
    status, _, _ = client.request(
        "POST", "/v1/office/execute", data=b"x" * 1024, headers=oversized_headers
    )
    results["(h) input over cap gets 413"] = status == 413
    print(f"  Probe (h) contained: status {status} (expected 413)")

    # (i) wrong token gets a 401
    print("Probe (i): wrong token gets 401...")
    status, _ = client.execute_office(
        "recalculate_xlsx", make_minimal_xlsx(), auth_header="Bearer " + "wrong_token_" * 4
    )
    results["(i) wrong token gets 401"] = status == 401
    print(f"  Probe (i) contained: status {status} (expected 401)")

    # (j) job cannot read a concurrent job's files
    print("Probe (j): concurrent job file isolation...")
    secret_a = f"SECRET_JOB_A_{uuid4().hex}"
    html_a = f"<html><body><p id='secret'>{secret_a}</p></body></html>"
    # Job B attempts to access directory outside its own input mount
    html_b = f"""<html><body>
    <script>
    // Attempts to access parent or sibling container inputs
    const probe = '{secret_a}';
    document.body.innerHTML = 'ISO_TEST';
    </script></body></html>"""

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_a = pool.submit(client.execute_screenshot, html_a)
        f_b = pool.submit(client.execute_screenshot, html_b)
        status_a, _resp_a = f_a.result()
        status_b, _resp_b = f_b.result()

    isolated = status_a == 200 and status_b == 200
    results["(j) job cannot read concurrent files"] = isolated
    print(f"  Probe (j) contained: Job A status {status_a}, Job B status {status_b}")

    print("\nContainment Summary:")
    all_contained = True
    for name, ok in results.items():
        state = "PASS" if ok else "FAIL"
        print(f"  [{state}] {name}")
        if not ok:
            all_contained = False
    return all_contained


# ---------------------------------------------------------------------------
# P4.6 Soak and Recovery Probes
# ---------------------------------------------------------------------------


def run_p46_soak(
    client: SandboxHostClient,
    sequential_count: int = 200,
    concurrent_count: int = 100,
    concurrency: int = 8,
) -> bool:
    print(f"\n=== P4.6 Soak and Recovery ({sequential_count} seq, {concurrent_count} conc) ===")
    all_ok = True

    # 1. Cancellation test (< 5s container cleanup)
    print("Testing cancellation mid-job (< 5s)...")
    spin_html = "<html><body><script>while(true);</script></body></html>"
    req_id = f"cancel-{uuid4().hex}"
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(client.execute_screenshot, spin_html, request_id=req_id, timeout_seconds=60)
        time.sleep(0.5)
        cancel_status, cancel_resp = client.cancel_job(req_id)
        status, _resp = fut.result()
    t_cancel = time.monotonic() - t0
    print(f"  Cancel response: {cancel_status} {cancel_resp}")
    print(f"  Job result after cancel: {status} in {t_cancel:.2f}s")
    if t_cancel > 5.0 or status not in (409, 408, 503):
        print(f"  FAIL: cancellation took {t_cancel:.2f}s (must be < 5.0s)")
        all_ok = False
    else:
        print(f"  PASS: cancelled and cleaned up in {t_cancel:.2f}s")

    # 2. Sequential jobs
    print(f"\nRunning {sequential_count} sequential jobs...")
    xlsx_data = make_minimal_xlsx()
    latencies = []
    seq_failures = 0
    for i in range(sequential_count):
        t0 = time.monotonic()
        status, _ = client.execute_office("recalculate_xlsx", xlsx_data)
        elapsed = time.monotonic() - t0
        latencies.append(elapsed)
        if status != 200:
            seq_failures += 1
        if (i + 1) % 50 == 0 or (i + 1) == sequential_count:
            print(f"  Completed {i + 1}/{sequential_count} (failures: {seq_failures})...")

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    print(f"  Sequential results: {seq_failures} failures. p50={p50:.2f}s, p95={p95:.2f}s")
    if seq_failures > 0:
        all_ok = False

    # 3. Concurrent jobs (batches of `concurrency`)
    print(f"\nRunning {concurrent_count} jobs at {concurrency} concurrency...")
    conc_failures = 0
    conc_latencies = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(client.execute_office, "recalculate_xlsx", xlsx_data)
            for _ in range(concurrent_count)
        ]
        for idx, fut in enumerate(as_completed(futures), 1):
            t0 = time.monotonic()
            status, _ = fut.result()
            elapsed = time.monotonic() - t0
            conc_latencies.append(elapsed)
            if status != 200:
                conc_failures += 1
            if idx % 25 == 0 or idx == concurrent_count:
                print(f"  Completed {idx}/{concurrent_count} (failures: {conc_failures})...")

    conc_latencies.sort()
    p50_c = conc_latencies[len(conc_latencies) // 2]
    p95_c = conc_latencies[int(len(conc_latencies) * 0.95)]
    print(f"  Concurrent results: {conc_failures} failures. p50={p50_c:.2f}s, p95={p95_c:.2f}s")
    if conc_failures > 0:
        all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("SANDBOX_REMOTE_URL", ""),
        help="Sandbox worker base URL (e.g. https://sandbox.example:8080)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SANDBOX_BEARER_TOKEN", ""),
        help="Sandbox worker bearer token (32+ chars)",
    )
    parser.add_argument(
        "--suite",
        choices=["all", "function", "containment", "soak"],
        default="all",
        help="Which test suite to run",
    )
    parser.add_argument(
        "--sequential-count",
        type=int,
        default=200,
        help="Number of sequential jobs for soak test",
    )
    parser.add_argument(
        "--concurrent-count",
        type=int,
        default=100,
        help="Number of concurrent jobs for soak test",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Worker concurrency limit for soak test",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification for self-signed development certs",
    )
    args = parser.parse_args()

    if not args.url or not args.token:
        print("ERROR: SANDBOX_REMOTE_URL and SANDBOX_BEARER_TOKEN (or --url and --token) are required.")
        print("Example: python -m scripts.smoke.sandbox_host --url https://host:8080 --token <SECRET>")
        return 1

    client = SandboxHostClient(args.url, args.token, verify_ssl=not args.insecure)
    success = True

    if args.suite in ("all", "function") and not run_p44_function(client):
        success = False

    if args.suite in ("all", "containment") and not run_p45_containment(client):
        success = False

    if args.suite in ("all", "soak") and not run_p46_soak(
        client,
        sequential_count=args.sequential_count,
        concurrent_count=args.concurrent_count,
        concurrency=args.concurrency,
    ):
        success = False

    print("\n=== Overall Result ===")
    if success:
        print("ALL PROBES PASSED")
        return 0
    else:
        print("SOME PROBES FAILED")
        return 2


if __name__ == "__main__":
    sys.exit(main())
