"""Render the requested manual audit from immutable responses and semantic reviews."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "docs/audit/tests"


def main():
    manifest = json.loads((ROOT / "audit-manifest.json").read_text(encoding="utf-8"))
    reviews = {}
    for filename in ("review-academic.json", "review-careers.json", "review-grad-jobs.json",
                     "review-programs.json"):
        path = ROOT / filename
        if path.exists():
            for review in json.loads(path.read_text(encoding="utf-8")):
                if review["id"] in reviews:
                    raise ValueError(f"Duplicate review: {review['id']}")
                reviews[review["id"]] = review
    rows = []
    for entry in manifest["primary_cases"]:
        path = ROOT / entry["primary_result"]
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        review = reviews.get(entry["id"], {"verdict": "pending", "rationale": "Awaiting semantic review."})
        rows.append({**entry, "prompt": raw.get("prompt", "Not run yet"),
                     "http_status": raw.get("http_status"), "seconds": raw.get("seconds"),
                     "provider_calls": len(raw.get("provider_calls", [])), **review})
    counts = Counter(r["verdict"] for r in rows)
    headers = ["# Manual advisor prompt ledger", "",
               ("74 distinct manually authored prompts. Each primary result has an independent semantic review; "
                "HTTP success and citation acceptance are not correctness grades."), "",
               ("**Mixed versions:** 22 primary cases use research-4; 52 use research-6. "
               "This is a defect-finding checklist, not a single-version accuracy benchmark. "
               "Thirteen targeted follow-ups are recorded separately below."), "",
               ("See [findings](findings-2026-10-21.md), [method](README.md), "
               "[full delivered answers](answers-2026-10-21.md), and "
               "[artifact manifest](audit-manifest.json)."), "",
               "| Reviewed result | Count |", "|---|---:|"]
    for status in ("correct", "iffy", "refusal_justified", "refusal_unnecessary", "error", "pending"):
        headers.append(f"| {status} | {counts[status]} |")
    headers.extend(["", ("`error` means no usable answer was delivered; `iffy` explains a material support, "
                    "scope or usefulness issue. Justified refusals are retained as successful boundaries, "
                    "but are not counted as substantive answers."), "",
                    "| Case | Prompt | Verdict | Seconds / calls | Review |", "|---|---|---|---|---|"])
    answers = ["# Delivered advisor answers", "",
               ("Exact delivered text for each primary case. Numbered citations are local to that answer. "
               "Source names and locators follow each answer; the raw artifact retains exact context and tool attempts. "
               "Reviews and verdicts are in [the ledger](ledger-2026-10-21.md)."), ""]
    for row in rows:
        esc = lambda text: str(text).replace("|", "\\|").replace("\n", " ")
        evidence = row["primary_result"]
        headers.append(f"| [{row['id']}]({evidence}) | {esc(row['prompt'])} | {row['verdict']} | "
                       f"{row['seconds']} / {row['provider_calls']} | {esc(row['rationale'])} |")
        answers.extend([f"## {row['id']} — {row['verdict']}", "", row["prompt"], "",
                        f"Prompt version: {row['prompt_version']}. [Raw response and evidence]({evidence}).", ""])
        path = ROOT / evidence
        if not path.exists():
            answers.extend(["Not run yet.", ""])
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        response = raw.get("response", {})
        text = response.get("answer") or response.get("reason") or response.get("detail") or "No delivered answer."
        answers.extend([text, ""])
        for i, citation in enumerate(response.get("citations", []), 1):
            answers.append(f"- [{i}] {citation['sourceName']}: {citation['locatorLabel']} "
                           f"(chunk `{citation['chunkId']}`).")
        answers.append("")
    headers.extend(["", "## Targeted retests", "",
                    ("Research versions 5 and 6 each retest A08, A09, C01, C02 and C03. "
                    "Two further retests check S03/S04 after the preparation-caller fix. "
                    "An explicit MATH 112 follow-up verifies the repaired tool was actually executed. "
                    "Original primary verdicts above remain intact."), "",
                    "| Version | Case | Verdict | Review |", "|---|---|---|---|"])
    answers.extend(["## Targeted follow-ups", "", "These preserve the original primary verdicts above.", ""])
    for filename in ("review-retests-careers.json", "review-retests-academic.json",
                     "review-retests-preparation.json"):
        path = ROOT / filename
        if path.exists():
            for review in json.loads(path.read_text(encoding="utf-8")):
                run = review.get("run", "")
                version = review.get("prompt_version", review.get("version", run.replace("retest-", "")))
                case_id = review.get("case_id", review["id"].split("-v")[0])
                evidence = review.get("raw_path", review.get("path", f"{run}/{case_id}.json"))
                evidence = evidence.removeprefix("docs/audit/tests/")
                headers.append(f"| {version} | [{review['id']}]({evidence}) | {review['verdict']} | "
                               f"{esc(review['rationale'])} |")
                raw = json.loads((ROOT / evidence).read_text(encoding="utf-8"))
                response = raw.get("response", {})
                delivered = (response.get("answer") or response.get("reason")
                             or response.get("detail") or "No delivered answer.")
                answers.extend([f"### {review['id']} — {review['verdict']}", "", raw["prompt"], "",
                                f"{version}. [Raw response and evidence]({evidence}).", "", delivered, ""])
                for i, citation in enumerate(response.get("citations", []), 1):
                    answers.append(f"- [{i}] {citation['sourceName']}: {citation['locatorLabel']} "
                                   f"(chunk `{citation['chunkId']}`).")
                answers.append("")
    headers.extend(["", "## Excluded infrastructure/harness attempts", "",
                    ("The three initial sandbox-network failures, the original harness token-ceiling cascade, "
                    "and resulting rapid rate-limited requests are retained in raw folders but excluded from "
                    "these 74 primary verdicts. The manifest identifies exactly which results replace them."), ""])
    (ROOT / "ledger-2026-10-21.md").write_text("\n".join(headers), encoding="utf-8")
    (ROOT / "answers-2026-10-21.md").write_text("\n".join(answers), encoding="utf-8")
    (ROOT / "ledger-2026-10-21.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"cases": len(rows), "verdicts": dict(counts)}, indent=2))


if __name__ == "__main__":
    main()
