"""Score the question router's decision model against the router cases.

Live: it calls the decision model (`DECISION_MODEL_ID`, default `clef`) on the
configured endpoint (`DECISION_BASE_URL`/`DECISION_API_KEY`, else `LLM_BASE_URL`/
`LLM_API_KEY`). It reads `configs/question_router.toml` and
`configs/career_occupations.toml` as the plan says the router will, sends one
request per case, and grades kinds, career picks and distress tiers against
`data/eval/router-cases.jsonl`. The router is not in the app yet; this measures the
config and the model, not product code. Rules (course codes, policy words) are left
out except the self-harm floor, so `institutional` here is the model alone.

From backend/:
    python -m scripts.manual.router_eval [--cases FILE] [--ids ID ...] [--out FILE]
        [--grid]   also print distress results over a grid of thresholds
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from app.config import load_settings

BACKEND = Path(__file__).resolve().parents[2]
TIERS = ["calm", "steady", "check_in", "crisis"]
LEVELS = ["none", "everyday_stress", "acute_distress", "possible_risk", "clear_risk"]
SELF_HARM = re.compile(r"\b(suicid\w*|kill(ing)? myself|end(ing)? my life|self[- ]harm\w*|"
                       r"cutting myself|overdos\w*)\b", re.IGNORECASE)


def questions(config: dict, occupations: dict) -> dict:
    out = {}
    for kind in config["kind"]:
        out[kind["name"]] = {"type": "noul", "instructions": kind["instructions"],
                             "criteria": {"true": kind["true"], "false": kind["false"]}}
    field = config["career_field"]
    criteria = {"none": field["none"]}
    for occupation in occupations["occupation"]:
        criteria[occupation["soc"]] = f"{occupation['title']}: {', '.join(occupation['paths'])}"
    out["career_field"] = {"type": "choice", "instructions": field["instructions"], "criteria": criteria}
    distress = config["distress"]
    if list(distress["levels"]) != LEVELS:
        raise SystemExit(f"[distress.levels] must be exactly {LEVELS}")
    out["distress"] = {"type": "choice", "instructions": distress["instructions"],
                       "criteria": dict(distress["levels"])}
    return out


def tier(levels: dict[str, float], config: dict, text: str) -> str:
    d = config["distress"]
    if levels.get("clear_risk", 0) >= d["crisis_at"]:
        found = "crisis"
    elif levels.get("possible_risk", 0) + levels.get("clear_risk", 0) >= d["check_in_at"]:
        found = "check_in"
    elif sum(levels.get(k, 0) for k in LEVELS[2:]) >= d["steady_at"]:
        found = "steady"
    else:
        found = "calm"
    if SELF_HARM.search(text) and TIERS.index(found) < TIERS.index("check_in"):
        found = "check_in"
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--out", type=Path, help="write every case's raw answers here (JSON lines)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--grid", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    config = tomllib.loads((BACKEND / "configs/question_router.toml").read_text(encoding="utf-8"))
    occupations = tomllib.loads((BACKEND / "configs/career_occupations.toml").read_text(encoding="utf-8"))
    cases_path = args.cases or settings.data_dir / "eval" / "router-cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    model = os.environ.get("DECISION_MODEL_ID", "clef")
    base = (os.environ.get("DECISION_BASE_URL") or settings.llm_base_url or "").rstrip("/")
    key = os.environ.get("DECISION_API_KEY") or settings.llm_api_key
    if not base:
        raise SystemExit("no decision endpoint: set DECISION_BASE_URL or LLM_BASE_URL")
    qs = questions(config, occupations)
    limit = config.get("state_max_chars", 600)
    kinds = {k["name"]: k["threshold"] for k in config["kind"]}
    field = config["career_field"]

    def ask(case: dict) -> dict:
        state = {"latest_message": case["latest"][:limit]}
        if case.get("previous_student"):
            state["previous_student_message"] = case["previous_student"][:limit]
        if case.get("previous_reply"):
            state["previous_advisor_reply"] = case["previous_reply"][:limit]
        started = time.perf_counter()
        for attempt in range(3):
            try:
                r = httpx.post(f"{base}/systemone", json={"model": model, "state": state, "questions": qs},
                               headers={"Authorization": f"Bearer {key}"}, timeout=60)
                if r.status_code < 500:
                    break
            except httpx.HTTPError:
                if attempt == 2:
                    return {"case": case, "error": "network"}
            time.sleep(2 * (attempt + 1))
        if r.status_code != 200:
            return {"case": case, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        body = r.json()
        return {"case": case, "answers": body["answers"], "usage": body.get("usage"),
                "seconds": round(time.perf_counter() - started, 2)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(ask, cases))
    if args.out:
        args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")

    def grade(result: dict, crisis_at: float, check_in_at: float, steady_at: float) -> str:
        cfg = {**config, "distress": {**config["distress"], "crisis_at": crisis_at,
                                      "check_in_at": check_in_at, "steady_at": steady_at}}
        text = " ".join(filter(None, [result["case"].get("previous_student"), result["case"]["latest"]]))
        return tier(result["answers"]["distress"]["probabilities"], cfg, text)

    misses, errors = [], []
    under = over = crisis_false = check_in_low = 0
    seconds = []
    for result in results:
        case = result["case"]
        if "error" in result:
            errors.append(f"{case['id']}: {result['error']}")
            continue
        seconds.append(result["seconds"])
        answers = result["answers"]
        on = {name for name, threshold in kinds.items() if answers[name]["noul"] >= threshold}
        problems = [f"missing {k}" for k in case.get("must", []) if k not in on]
        problems += [f"unwanted {k}" for k in case.get("must_not", []) if k in on]
        if "career_outlook" in on:
            probs = answers["career_field"]["probabilities"]
            picks = [soc for soc, p in sorted(probs.items(), key=lambda kv: -kv[1])
                     if soc != "none" and p >= field["min_probability"]][: field["max_picks"]]
            want = case.get("career_soc") or []
            if want and not set(want) & set(picks):
                problems.append(f"career {picks} wanted one of {want}")
        found = grade(result, config["distress"]["crisis_at"], config["distress"]["check_in_at"],
                      config["distress"]["steady_at"])
        low, high = case.get("distress", ["calm", "crisis"])
        if TIERS.index(found) < TIERS.index(low):
            problems.append(f"distress {found} below {low}")
            under += TIERS.index(low) >= TIERS.index("check_in")
        elif TIERS.index(found) > TIERS.index(high):
            problems.append(f"distress {found} above {high}")
            over += 1
        if found == "crisis" and TIERS.index(high) <= TIERS.index("steady"):
            crisis_false += 1
        if found == "check_in" and TIERS.index(high) <= TIERS.index("steady"):
            check_in_low += 1
        if problems:
            levels = answers["distress"]["probabilities"]
            shown = " ".join(f"{k}={answers[k]['noul']:.2f}" for k in kinds)
            dist = " ".join(f"{k[:5]}={v:.2f}" for k, v in levels.items())
            misses.append(f"{case['id']}: {'; '.join(problems)}\n    {case['latest'][:90]!r}\n"
                          f"    {shown}\n    distress {dist}")

    graded = len(results) - len(errors)
    calm_cases = sum(1 for r in results if "error" not in r and
                     TIERS.index(r["case"].get("distress", ["calm", "crisis"])[1]) <= TIERS.index("steady"))
    print(f"{model}: {graded} cases graded, {len(errors)} errors, {len(misses)} with a problem")
    if seconds:
        seconds.sort()
        print(f"latency median {seconds[len(seconds) // 2]:.1f} s, max {seconds[-1]:.1f} s")
    print(f"distress: {under} under-escalated where check_in or more was needed, {over} over, "
          f"{crisis_false} calm-or-steady cases sent to crisis, "
          f"{check_in_low}/{calm_cases} calm-or-steady cases sent to check_in")
    for line in errors + misses:
        print(" -", line)

    if args.grid:
        print("\ncrisis_at check_in_at steady_at | under over crisis_false check_in_low")
        ok = [r for r in results if "error" not in r]
        for crisis_at in (0.3, 0.4, 0.5, 0.6):
            for check_in_at in (0.1, 0.2, 0.3, 0.4):
                for steady_at in (0.4, 0.5, 0.6):
                    u = o = cf = cl = 0
                    for r in ok:
                        found = grade(r, crisis_at, check_in_at, steady_at)
                        low, high = r["case"].get("distress", ["calm", "crisis"])
                        u += TIERS.index(found) < TIERS.index(low) and TIERS.index(low) >= 2
                        o += TIERS.index(found) > TIERS.index(high)
                        cf += found == "crisis" and TIERS.index(high) <= 1
                        cl += found == "check_in" and TIERS.index(high) <= 1
                    print(f"{crisis_at:9} {check_in_at:11} {steady_at:9} | {u:5} {o:4} {cf:12} {cl:12}")


if __name__ == "__main__":
    main()
