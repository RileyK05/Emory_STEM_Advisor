"""Score the question router against the router cases, live.

Runs the app's own `route_question` (rules plus the decision model) on each case
in `data/eval/router-cases.jsonl` and grades kinds, career picks and distress
tiers. The decision model is `DECISION_MODEL_ID` (default here: `clef`) on
`DECISION_BASE_URL`/`DECISION_API_KEY`, else `LLM_BASE_URL`/`LLM_API_KEY`.
Calls run concurrently; nothing is written to the database.

From backend/:
    python -m scripts.manual.router_eval [--cases FILE] [--ids ID ...] [--out FILE]
        [--model clef-flash] [--grid]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from app.config import load_settings
from app.generation.question_router import (
    TIERS,
    distress_tier,
    load_router_config,
    route_question,
)
from app.llm.factory import build_decision_provider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--out", type=Path, help="write every case's route here (JSON lines)")
    parser.add_argument("--model", help="override DECISION_MODEL_ID")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--grid", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    model = args.model or settings.decision_model_id or os.environ.get("DECISION_MODEL_ID") or "clef"
    settings = replace(settings, decision_model_id=model,
                       decision_timeout_seconds=max(settings.decision_timeout_seconds, 60))
    decider = build_decision_provider(settings)
    if decider is None:
        raise SystemExit("no decision endpoint: set DECISION_BASE_URL or LLM_BASE_URL")
    config = load_router_config()
    cases_path = args.cases or settings.data_dir / "eval" / "router-cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]

    def ask(case: dict) -> dict:
        started = time.perf_counter()
        route = route_question(decider, case["latest"], config=config,
                               previous_student=case.get("previous_student"),
                               previous_reply=case.get("previous_reply"))
        return {"case": case, "route": route.details(),
                "tokens": route.usage.prompt_tokens if route.usage else None,
                "seconds": round(time.perf_counter() - started, 2)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(ask, cases))
    if args.out:
        args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
                            encoding="utf-8")

    def below(found: str, low: str) -> bool:
        return TIERS.index(found) < TIERS.index(low)

    misses, fallbacks = [], 0
    under = over = crisis_false = check_in_low = calm_cases = 0
    for result in results:
        case, route = result["case"], result["route"]
        fallbacks += route["source"] != "model"
        on = set(route["categories"])
        problems = [f"missing {k}" for k in case.get("must", []) if k not in on]
        problems += [f"unwanted {k}" for k in case.get("must_not", []) if k in on]
        want = case.get("career_soc") or []
        if want and not set(want) & set(route["career_soc"]):
            problems.append(f"career {route['career_soc']} wanted one of {want}")
        low, high = case.get("distress", ["calm", "crisis"])
        found = route["distress"]
        if below(found, low):
            problems.append(f"distress {found} below {low}")
            under += TIERS.index(low) >= TIERS.index("check_in")
        elif TIERS.index(found) > TIERS.index(high):
            problems.append(f"distress {found} above {high}")
            over += 1
        if TIERS.index(high) <= TIERS.index("steady"):
            calm_cases += 1
            crisis_false += found == "crisis"
            check_in_low += found == "check_in"
        if problems:
            shown = " ".join(f"{k}={v:.2f}" for k, v in route["scores"].items())
            dist = " ".join(f"{k[:5]}={v:.2f}" for k, v in route["distress_levels"].items())
            misses.append(f"{case['id']}: {'; '.join(problems)} [{route['source']}]\n"
                          f"    {case['latest'][:90]!r}  rules={route['rule_hits']}\n"
                          f"    {shown}\n    distress {dist}")

    seconds = sorted(r["seconds"] for r in results)
    tokens = [r["tokens"] for r in results if r["tokens"]]
    print(f"{model}: {len(results)} cases, {len(misses)} with a problem, {fallbacks} not from the model")
    print(f"latency median {seconds[len(seconds) // 2]:.1f} s, max {seconds[-1]:.1f} s; "
          f"input tokens mean {sum(tokens) / max(len(tokens), 1):.0f}")
    print(f"distress: {under} under-escalated where check_in or more was needed, {over} over; "
          f"{crisis_false} calm-or-steady cases sent to crisis, "
          f"{check_in_low}/{calm_cases} sent to check_in")
    for line in misses:
        print(" -", line)

    if args.grid:
        print("\ncrisis_at check_in_at steady_at | under over crisis_false check_in_low")
        for crisis_at in (0.3, 0.4, 0.5, 0.6):
            for check_in_at in (0.1, 0.2, 0.3, 0.4):
                for steady_at in (0.4, 0.5, 0.6):
                    grid = replace(config, crisis_at=crisis_at, check_in_at=check_in_at,
                                   steady_at=steady_at)
                    u = o = cf = cl = 0
                    for r in results:
                        levels = r["route"]["distress_levels"]
                        if not levels:
                            continue
                        found = distress_tier(levels, grid)
                        low, high = r["case"].get("distress", ["calm", "crisis"])
                        u += below(found, low) and TIERS.index(low) >= 2
                        o += TIERS.index(found) > TIERS.index(high)
                        cf += found == "crisis" and TIERS.index(high) <= 1
                        cl += found == "check_in" and TIERS.index(high) <= 1
                    print(f"{crisis_at:9} {check_in_at:11} {steady_at:9} | {u:5} {o:4} {cf:12} {cl:12}")


if __name__ == "__main__":
    main()
