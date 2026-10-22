"""Pure, bounded allocation of reviewed program rules to academic attempts.

Every supported assignment is considered within an explicit search budget.
Exhaustion is unresolved; it is never evidence that a requirement is outstanding.
A course identity can be consumed once, including across repeated attempts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from app.programs.rules import (
    CoverageDisposition,
    RuleNode,
    RuleNodeType,
    validate_rule_structure,
)

ENGINE_VERSION = "2"
GRADE_POINTS = {
    "A+": 4.0, "A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7,
    "C+": 2.3, "C": 2.0, "C-": 1.7, "D+": 1.3, "D": 1.0, "F": 0.0,
}
_PASS = {"S", "P", "PASS", "CR"}
_FAIL = {"F", "U", "NC", "W", "WD"}
_PROVISIONAL = {"in_progress_satisfied", "conditional_satisfied"}
_RANK = {"not_selected": 0, "outstanding": 1, "unresolved": 2,
         "conditional_satisfied": 3, "in_progress_satisfied": 4, "satisfied": 5}


@dataclass(frozen=True)
class EvaluatedAttempt:
    attempt_id: str
    course_code: str
    term: str
    grade: str | None
    attempted_credits: float
    earned_credits: float
    status: str
    is_verified: bool
    provenance: str

    @property
    def is_completed(self) -> bool:
        return self.status in {"completed", "transfer", "test_credit", "repeated"}

    @property
    def is_in_progress_or_planned(self) -> bool:
        return self.status in {"in_progress", "planned"}

    @property
    def credits(self) -> float:
        return self.earned_credits if self.is_completed else self.attempted_credits


@dataclass
class RuleEvaluationResult:
    node_id: str
    parent_node_id: str | None
    node_type: str
    title: str
    status: str
    allocated_attempt_ids: list[str] = field(default_factory=list)
    allocated_credits: float = 0.0
    required_count: int | None = None
    satisfied_count: int | None = None
    unresolved_reason: str | None = None
    raw_position: int | None = None
    children: list[RuleEvaluationResult] = field(default_factory=list)


@dataclass
class AuditEngineResult:
    overall_status: str
    completed_credits: float
    in_progress_credits: float
    unverified_credits: float
    has_unverified_contributions: bool
    has_in_progress_contributions: bool
    rule_evaluations: list[RuleEvaluationResult]
    blockers: list[str]
    assumptions: list[str]
    rules_digest: str
    results_summary: dict[str, Any]


@dataclass(frozen=True)
class _Candidate:
    result: RuleEvaluationResult
    used: frozenset[str] = frozenset()


class _SearchLimit(Exception):
    pass


def _flatten(node: RuleEvaluationResult) -> list[RuleEvaluationResult]:
    return [node, *(item for child in node.children for item in _flatten(child))]


def _score(candidate: _Candidate):
    rows = _flatten(candidate.result)
    leaves = [r for r in rows if not r.children and r.status != "not_selected"]
    return (
        _RANK[candidate.result.status],
        sum(r.status == "satisfied" for r in leaves),
        sum(r.satisfied_count or 0 for r in leaves),
        sum(r.allocated_credits for r in leaves),
        -len(candidate.used),
    )


class DeterministicAuditEngine:
    def __init__(self, rules_tree: RuleNode | dict[str, Any], attempts: list[dict[str, Any]],
                 rules_digest: str = "", *, max_search_states: int = 50_000):
        self.root = RuleNode.from_dict(rules_tree) if isinstance(rules_tree, dict) else rules_tree
        validate_rule_structure(self.root)
        if type(max_search_states) is not int or max_search_states < 1:
            raise ValueError("max_search_states must be a positive integer")
        self.rules_digest, self.max_search_states = rules_digest, max_search_states
        self.states = 0
        self.evaluated_attempts = self._normalize(attempts)

    @staticmethod
    def _normalize(attempts):
        result = []
        seen_ids = set()
        for raw in attempts:
            def value(snake, camel, default=None, raw=raw):
                return raw.get(snake, raw.get(camel, default))

            code = value("resolved_course_code", "resolvedCourseCode")
            code = code or value("submitted_course_code", "submittedCourseCode", "")
            identity = str(value("attempt_id", "attemptId", ""))
            if not identity or identity in seen_ids:
                raise ValueError("attempt identities must be present and unique")
            seen_ids.add(identity)
            if not isinstance(code, str) or not code.strip():
                raise ValueError("attempt requires a course identity")
            attempted = float(value("attempted_credits", "attemptedCredits", 0.0))
            earned = float(value("earned_credits", "earnedCredits", 0.0))
            if any(not math.isfinite(c) or c < 0 for c in (attempted, earned)):
                raise ValueError("attempt credits must be finite and nonnegative")
            status = raw.get("status", "completed")
            if status in {"failed", "withdrawn"}:
                continue
            if status not in {"completed", "transfer", "test_credit", "in_progress", "planned", "repeated"}:
                raise ValueError("unsupported attempt status")
            grade = str(raw["grade"]).strip().upper() if raw.get("grade") else None
            if grade in _FAIL:
                continue
            result.append(EvaluatedAttempt(
                identity, code.strip().upper(), str(raw.get("term", "")), grade,
                attempted, earned, status, bool(value("is_verified", "isVerified", False)),
                str(raw.get("provenance", "student_manual")),
            ))
        # Keep every attempt as an alternative. Selection enforces unique course identities.
        return sorted(result, key=lambda a: (
            not (a.is_completed and a.is_verified), not a.is_completed,
            a.status == "planned", -GRADE_POINTS.get(a.grade or "", -1),
            a.course_code, a.term, a.attempt_id,
        ))

    def _tick(self):
        self.states += 1
        if self.states > self.max_search_states:
            raise _SearchLimit

    @staticmethod
    def _row(node, parent, status, **kwargs):
        return RuleEvaluationResult(node.node_id, parent, node.node_type.value, node.title,
                                    status, raw_position=node.raw_position, **kwargs)

    def _not_selected(self, node, parent):
        return self._row(node, parent, "not_selected", children=[
            self._not_selected(c, node.node_id) for c in node.children])

    @staticmethod
    def _attempt_status(attempt, min_grade):
        if attempt.status == "repeated":
            return "unresolved", "The repeated attempt requires a reviewed repeat-credit policy."
        if attempt.status == "planned":
            return "conditional_satisfied", None
        if attempt.status == "in_progress":
            return "in_progress_satisfied", None
        if min_grade and (attempt.grade not in GRADE_POINTS or min_grade not in GRADE_POINTS):
            return "unresolved", "A letter-grade threshold needs an evidenced letter grade; pass/fail equivalence is unknown."
        if min_grade and GRADE_POINTS[attempt.grade] < GRADE_POINTS[min_grade]:
            return "outstanding", None
        if (attempt.grade and attempt.grade not in GRADE_POINTS and attempt.grade not in _PASS
                and attempt.status not in {"transfer", "test_credit"}):
            return "unresolved", "The recorded grade has no supported passing policy."
        if attempt.attempted_credits > 0 and attempt.earned_credits == 0:
            return "unresolved", "Completed work has no awarded credit; review the attempt and repeat policy."
        if not attempt.grade and attempt.earned_credits == 0:
            return "unresolved", "Completion has neither an awarded credit nor a passing grade."
        return ("satisfied" if attempt.is_verified else "conditional_satisfied"), None

    @staticmethod
    def _aggregate(statuses, missing=False):
        if "unresolved" in statuses:
            return "unresolved"
        if missing or "outstanding" in statuses:
            return "outstanding"
        if not statuses:
            return "unresolved"
        if "conditional_satisfied" in statuses:
            return "conditional_satisfied"
        if "in_progress_satisfied" in statuses:
            return "in_progress_satisfied"
        return "satisfied"

    def _deduplicate(self, candidates):
        best = {}
        for candidate in candidates:
            key = (candidate.used, candidate.result.status)
            previous = best.get(key)
            if previous is None or _score(candidate) > _score(previous):
                best[key] = candidate
        return list(best.values())

    def _options(self, node, parent=None, inherited_grade=None):
        self._tick()
        if node.coverage_disposition == CoverageDisposition.INFORMATIONAL:
            return [_Candidate(self._not_selected(node, parent))]
        if (node.node_type == RuleNodeType.UNRESOLVED
                or node.coverage_disposition == CoverageDisposition.UNRESOLVED):
            return [_Candidate(self._row(node, parent, "unresolved",
                                         unresolved_reason=node.unresolved_reason))]
        grade = node.min_grade or inherited_grade
        if grade and grade not in GRADE_POINTS:
            return [_Candidate(self._row(node, parent, "unresolved",
                                         unresolved_reason="The minimum-grade policy is unsupported."))]
        if node.children:
            return self._container_options(node, parent, grade)
        return self._leaf_options(node, parent, grade)

    def _leaf_options(self, node, parent, grade):
        eligible = []
        for attempt in self.evaluated_attempts:
            if node.course_codes and attempt.course_code not in node.course_codes:
                continue
            status, reason = self._attempt_status(attempt, grade)
            if status != "outstanding":
                eligible.append((attempt, status, reason))
        needed = node.count or 1
        if node.node_type == RuleNodeType.ALL_OF:
            needed = len(node.course_codes)
        credit_rule = node.node_type == RuleNodeType.CREDIT_THRESHOLD
        limit = len(eligible) if credit_rule else min(needed, len(eligible))
        result = []
        for size in range(limit + 1):
            for selected in combinations(eligible, size):
                self._tick()
                used = frozenset(a.course_code for a, _, _ in selected)
                if len(used) != size:
                    continue
                credits = sum(a.credits for a, _, _ in selected)
                meets = credits >= node.min_credits if credit_rule else size >= needed
                statuses = [s for _, s, _ in selected]
                status = self._aggregate(statuses, missing=not meets)
                # Supersets of a sufficient subset cannot improve allocation.
                if (credit_rule and meets
                        and any(credits - a.credits >= node.min_credits for a, _, _ in selected)):
                    continue
                reason = next((r for _, _, r in selected if r), None)
                row = self._row(
                    node, parent, status, allocated_attempt_ids=[a.attempt_id for a, _, _ in selected],
                    allocated_credits=credits, required_count=None if credit_rule else needed,
                    satisfied_count=sum(s == "satisfied" for _, s, _ in selected),
                    unresolved_reason=reason if status == "unresolved" else None,
                )
                result.append(_Candidate(row, used))
        return self._deduplicate(result)

    def _container_options(self, node, parent, grade):
        children = list(node.children)
        child_options = [self._options(child, node.node_id, grade) for child in children]
        required = len(children) if node.node_type == RuleNodeType.ALL_OF else (node.count or 1)
        result = []

        def combine(index, selected, used, chosen):
            self._tick()
            if index == len(children):
                active = [r.status for r in selected if r.status != "not_selected"]
                status = self._aggregate(active, missing=chosen < required)
                reason = "One or more required clauses are unresolved." if status == "unresolved" else None
                row = self._row(
                    node, parent, status, children=list(selected),
                    allocated_attempt_ids=[i for r in selected for i in r.allocated_attempt_ids],
                    allocated_credits=sum(r.allocated_credits for r in selected),
                    required_count=required,
                    satisfied_count=sum(s == "satisfied" for s in active),
                    unresolved_reason=reason,
                )
                result.append(_Candidate(row, used))
                return
            child = children[index]
            if node.node_type != RuleNodeType.ALL_OF:
                combine(index + 1, [*selected, self._not_selected(child, node.node_id)], used, chosen)
                if chosen >= required:
                    return
            for candidate in child_options[index]:
                if candidate.used & used:
                    continue
                is_required = candidate.result.status != "not_selected"
                combine(index + 1, [*selected, candidate.result], used | candidate.used,
                        chosen + int(is_required))

        # Informational clauses do not count as mandatory requirements.
        if node.node_type == RuleNodeType.ALL_OF:
            required -= sum(c.coverage_disposition == CoverageDisposition.INFORMATIONAL for c in children)
        combine(0, [], frozenset(), 0)
        return self._deduplicate(result)

    def _witnesses(self, node, used, minimum_rank, parent=None, inherited_grade=None):
        """Find complete witnesses lazily before enumerating partial allocations.

        Ordinary all-of programs with independent requirements take linear work.
        Backtracking still explores every necessary alternative when courses compete.
        """
        self._tick()
        grade = node.min_grade or inherited_grade
        if node.coverage_disposition == CoverageDisposition.INFORMATIONAL:
            yield _Candidate(self._not_selected(node, parent))
            return
        if (node.node_type == RuleNodeType.UNRESOLVED
                or node.coverage_disposition == CoverageDisposition.UNRESOLVED
                or (grade and grade not in GRADE_POINTS)):
            if minimum_rank <= _RANK["unresolved"]:
                yield _Candidate(self._row(node, parent, "unresolved", unresolved_reason=(
                    node.unresolved_reason or "The minimum-grade policy is unsupported.")))
            return
        if node.children:
            active = [i for i, c in enumerate(node.children)
                      if c.coverage_disposition != CoverageDisposition.INFORMATIONAL]
            if not active:
                if minimum_rank <= _RANK["unresolved"]:
                    yield _Candidate(self._row(node, parent, "unresolved",
                        unresolved_reason="No executable requirements have been reviewed."))
                return
            needed = len(active) if node.node_type == RuleNodeType.ALL_OF else (node.count or 1)
            for indices in combinations(active, needed):
                self._tick()

                def combine(position, rows, consumed, indices=indices):
                    self._tick()
                    if position == len(indices):
                        children = [rows.get(i, self._not_selected(c, node.node_id))
                                    for i, c in enumerate(node.children)]
                        statuses = [rows[i].status for i in indices]
                        status = self._aggregate(statuses)
                        yield _Candidate(self._row(
                            node, parent, status, children=children, required_count=needed,
                            satisfied_count=sum(s == "satisfied" for s in statuses),
                            allocated_attempt_ids=[a for c in children for a in c.allocated_attempt_ids],
                            allocated_credits=sum(c.allocated_credits for c in children),
                            unresolved_reason="A required clause is unresolved." if status == "unresolved" else None,
                        ), consumed)
                        return
                    index = indices[position]
                    for child in self._witnesses(
                            node.children[index], used | consumed, minimum_rank, node.node_id, grade):
                        yield from combine(position + 1, {**rows, index: child.result},
                                           consumed | child.used)

                yield from combine(0, {}, frozenset())
            return
        eligible = []
        for attempt in self.evaluated_attempts:
            if attempt.course_code in used or (
                    node.course_codes and attempt.course_code not in node.course_codes):
                continue
            status, reason = self._attempt_status(attempt, grade)
            if _RANK[status] >= minimum_rank:
                eligible.append((attempt, status, reason))
        needed = len(node.course_codes) if node.node_type == RuleNodeType.ALL_OF else (node.count or 1)
        credit_rule = node.node_type == RuleNodeType.CREDIT_THRESHOLD
        if credit_rule:
            by_code = {}
            for attempt, _, _ in eligible:
                by_code[attempt.course_code] = max(by_code.get(attempt.course_code, 0), attempt.credits)
            if sum(by_code.values()) < node.min_credits:
                return
        elif len({a.course_code for a, _, _ in eligible}) < needed:
            return
        sizes = range(1, len(eligible) + 1) if credit_rule else (needed,)
        for size in sizes:
            for selected in combinations(eligible, size):
                self._tick()
                consumed = frozenset(a.course_code for a, _, _ in selected)
                credits = sum(a.credits for a, _, _ in selected)
                if len(consumed) != size or (credit_rule and credits < node.min_credits):
                    continue
                status = self._aggregate([s for _, s, _ in selected])
                yield _Candidate(self._row(
                    node, parent, status, allocated_attempt_ids=[a.attempt_id for a, _, _ in selected],
                    allocated_credits=credits, required_count=None if credit_rule else needed,
                    satisfied_count=sum(s == "satisfied" for _, s, _ in selected),
                    unresolved_reason=next((r for _, _, r in selected if r), None),
                ), consumed)

    def run(self) -> AuditEngineResult:
        self.states = 0
        limited = False
        try:
            chosen = None
            for rank in (5, 4, 3, 2):
                chosen = next(self._witnesses(self.root, frozenset(), rank), None)
                if chosen is not None:
                    break
            if chosen is None:
                chosen = max(self._options(self.root), key=_score)
        except _SearchLimit:
            limited = True
            chosen = _Candidate(self._row(
                self.root, None, "unresolved",
                unresolved_reason="Allocation search limit reached; no completion or deficit conclusion is verified.",
            ))
        evaluations = _flatten(chosen.result)
        if chosen.result.status == "not_selected":
            chosen = _Candidate(self._row(self.root, None, "unresolved",
                unresolved_reason="No executable requirements have been reviewed."))
            evaluations = [chosen.result]
        ids = set(chosen.result.allocated_attempt_ids)
        allocated = [a for a in self.evaluated_attempts if a.attempt_id in ids]
        completed = sum(a.earned_credits for a in allocated if a.is_completed)
        progress = sum(a.attempted_credits for a in allocated if a.is_in_progress_or_planned)
        unverified = sum(a.earned_credits for a in allocated if a.is_completed and not a.is_verified)
        blockers = [f"[{r.title}] {r.unresolved_reason or 'Requirement not satisfied'}"
                    for r in evaluations if r.status in {"outstanding", "unresolved"}]
        assumptions = []
        for a in allocated:
            if not a.is_verified:
                assumptions.append(f"Verify student-supplied attempt {a.course_code} ({a.term}).")
            if a.is_in_progress_or_planned:
                assumptions.append(f"Assumes {a.status.replace('_', ' ')} attempt {a.course_code} ({a.term}) completes successfully and meets its grade threshold.")
        summary = {
            "engineVersion": ENGINE_VERSION, "searchStates": self.states,
            "searchLimitReached": limited, "totalRules": len(evaluations),
            "satisfiedRules": sum(r.status == "satisfied" for r in evaluations),
            "inProgressSatisfiedRules": sum(r.status == "in_progress_satisfied" for r in evaluations),
            "conditionalRules": sum(r.status == "conditional_satisfied" for r in evaluations),
            "outstandingRules": sum(r.status == "outstanding" for r in evaluations),
            "unresolvedRules": sum(r.status == "unresolved" for r in evaluations),
            "completedCredits": completed, "inProgressCredits": progress, "unverifiedCredits": unverified,
        }
        return AuditEngineResult(
            chosen.result.status, completed, progress, unverified,
            any(a.is_completed and not a.is_verified for a in allocated),
            any(a.is_in_progress_or_planned for a in allocated), evaluations,
            blockers, assumptions, self.rules_digest, summary,
        )
