"""General-advice (perspective) blocks: what they may say, and how many a route allows."""

import re

from app.advising.research_types import (
    FinalDecision,
)
from app.atlas.requisites import canonical_codes_in

_PERSPECTIVE_RULE_RE = re.compile(
    r"\b(emory requires|you must|must take|required|requirement|deadline|credit hours?|"
    r"prerequisite|gpa|catalog says|policy says)\b", re.IGNORECASE)


def perspective_content_problems(decision: FinalDecision) -> list[str]:
    """General advice may only be perspective: no numbers, courses, rules or citations."""
    problems = []
    for block in decision.blocks:
        if block.kind != "perspective":
            continue
        if block.evidence_ids or block.candidate_ids:
            problems.append("general advice cites nothing; cite records in a fact block")
        elif (canonical_codes_in(block.text) or re.search(r"[0-9%$]", block.text)
              or _PERSPECTIVE_RULE_RE.search(block.text)):
            problems.append("general advice cannot state numbers, courses or Emory rules; put that "
                            "in a fact block that cites its record")
    return problems


def perspective_problems(decision: FinalDecision, *, allowed: int, must_search: frozenset[str],
                         searched: set[str], must_cite: frozenset[str],
                         found: dict[str, set[str]]) -> list[str]:
    """The route's obligations: how much general advice, what to search, what to cite.

    `searched` holds the advisor_search kinds the run used; `found` maps a source
    kind to the evidence IDs of that kind the run acquired.
    """
    problems = []
    if sum(block.kind == "perspective" for block in decision.blocks) > allowed:
        problems.append("this question needs records, not general advice; move it into a fact "
                        "with evidence_ids or remove it" if not allowed else
                        f"at most {allowed} perspective blocks of general advice for this question")
    problems += perspective_content_problems(decision)
    if decision.outcome != "declined":
        for kind in sorted(must_search - searched):
            problems.append(f"search {kind} sources before answering (advisor_search with "
                            f"kind={kind})")
        cited = {e for block in decision.blocks if block.kind == "fact" for e in block.evidence_ids}
        for kind in sorted(must_cite):
            if found.get(kind) and not found[kind] & cited:
                problems.append(f"you read {kind} records; answer from them first, in a fact block "
                                "that cites them")
    return problems
