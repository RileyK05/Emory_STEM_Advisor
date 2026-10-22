"""Reviewed program rules. Course mentions alone never establish executable semantics."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.atlas.records import normalize_code
from app.util.digest import canonical_json_bytes


class RuleNodeType(StrEnum):
    EXACT_COURSE = "exact_course"
    ALL_OF = "all_of"
    ANY_OF = "any_of"
    CHOOSE_COUNT = "choose_count"
    CREDIT_THRESHOLD = "credit_threshold"
    UNRESOLVED = "unresolved"


class CoverageDisposition(StrEnum):
    SUPPORTED = "supported"
    UNRESOLVED = "unresolved"
    INFORMATIONAL = "informational"
    WAIVED = "waived"


class RuleValidationError(ValueError):
    """A malformed, unsupported, or unevidenced executable requirement."""


@dataclass(frozen=True)
class RuleNode:
    node_id: str
    node_type: RuleNodeType
    title: str
    description: str | None = None
    section: str | None = None
    raw_position: int | None = None
    coverage_disposition: CoverageDisposition = CoverageDisposition.SUPPORTED
    course_codes: tuple[str, ...] = ()
    count: int | None = None
    min_credits: float | None = None
    min_grade: str | None = None
    children: tuple[RuleNode, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    unresolved_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeId": self.node_id, "nodeType": self.node_type.value, "title": self.title,
            "description": self.description, "section": self.section,
            "rawPosition": self.raw_position,
            "coverageDisposition": self.coverage_disposition.value,
            "courseCodes": list(self.course_codes), "count": self.count,
            "minCredits": self.min_credits, "minGrade": self.min_grade,
            "children": [c.to_dict() for c in self.children], "evidence": list(self.evidence),
            "unresolvedReason": self.unresolved_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, _depth: int = 0, _remaining=None) -> RuleNode:
        if not isinstance(data, dict) or _depth > 32:
            raise RuleValidationError("rule nodes must be objects with depth at most 32")
        remaining = [256] if _remaining is None else _remaining
        remaining[0] -= 1
        if remaining[0] < 0:
            raise RuleValidationError("rule trees support at most 256 nodes")

        def value(camel, snake=None, default=None):
            return data.get(camel, data.get(snake, default) if snake else default)

        try:
            node_type = RuleNodeType(value("nodeType", "node_type"))
            disposition = CoverageDisposition(value(
                "coverageDisposition", "coverage_disposition", "supported"))
        except (ValueError, TypeError) as exc:
            raise RuleValidationError("invalid nodeType or coverageDisposition") from exc
        codes = value("courseCodes", "course_codes", [])
        children = data.get("children", [])
        evidence = data.get("evidence", [])
        if not all(isinstance(v, (list, tuple)) for v in (codes, children, evidence)):
            raise RuleValidationError("courseCodes, children and evidence must be arrays")
        if len(children) > 256 or len(codes) > 256:
            raise RuleValidationError("a rule node supports at most 256 options")
        normalized = []
        for code in codes:
            code = normalize_code(code) if isinstance(code, str) else None
            if not code:
                raise RuleValidationError("invalid course code")
            normalized.append(code)
        return cls(
            node_id=value("nodeId", "node_id", ""), node_type=node_type,
            title=data.get("title", ""), description=data.get("description"),
            section=data.get("section"), raw_position=value("rawPosition", "raw_position"),
            coverage_disposition=disposition, course_codes=tuple(normalized),
            count=data.get("count"), min_credits=value("minCredits", "min_credits"),
            min_grade=value("minGrade", "min_grade"),
            children=tuple(cls.from_dict(c, _depth=_depth + 1, _remaining=remaining) for c in children),
            evidence=tuple(evidence),
            unresolved_reason=value("unresolvedReason", "unresolved_reason"),
        )


def validate_rule_structure(root: RuleNode) -> None:
    """Validate the executable language separately from repository evidence lookup."""
    seen: set[str] = set()

    def walk(node: RuleNode, depth: int):
        if (not isinstance(node.node_id, str) or not node.node_id.strip()
                or node.node_id in seen or depth > 32 or len(seen) >= 256):
            raise RuleValidationError("rule IDs must be unique; maximum 256 nodes and depth 32")
        seen.add(node.node_id)
        if node.children and node.course_codes:
            raise RuleValidationError("use child rules or course options, not both in one node")
        if not isinstance(node.title, str) or not node.title.strip():
            raise RuleValidationError("each rule requires a title")
        if len(set(node.course_codes)) != len(node.course_codes):
            raise RuleValidationError("duplicate course options do not count as distinct choices")
        if any(normalize_code(c) != c for c in node.course_codes):
            raise RuleValidationError("invalid course code")
        if node.raw_position is not None and (
                type(node.raw_position) is not int or node.raw_position < 0):
            raise RuleValidationError("rawPosition must be a nonnegative integer")
        if node.coverage_disposition == CoverageDisposition.WAIVED:
            raise RuleValidationError("student waivers cannot waive a published catalog rule")
        if (node.node_type == RuleNodeType.UNRESOLVED
                or node.coverage_disposition == CoverageDisposition.UNRESOLVED):
            if (node.coverage_disposition != CoverageDisposition.INFORMATIONAL
                    and not node.unresolved_reason):
                raise RuleValidationError("unresolved rules require an unresolvedReason")
        elif node.coverage_disposition != CoverageDisposition.INFORMATIONAL:
            if node.count is not None and node.node_type != RuleNodeType.CHOOSE_COUNT:
                raise RuleValidationError("count is executable only on choose_count rules")
            if node.min_credits is not None and node.node_type != RuleNodeType.CREDIT_THRESHOLD:
                raise RuleValidationError("minCredits is executable only on credit_threshold rules")
            options = len(node.course_codes) + len(node.children)
            if node.node_type == RuleNodeType.EXACT_COURSE:
                if len(node.course_codes) != 1 or node.children:
                    raise RuleValidationError("exact_course requires one course and no children")
            elif node.node_type in (RuleNodeType.ALL_OF, RuleNodeType.ANY_OF):
                if not options:
                    raise RuleValidationError("empty requirement containers are not executable")
            elif node.node_type == RuleNodeType.CHOOSE_COUNT:
                if type(node.count) is not int or not 1 <= node.count <= options:
                    raise RuleValidationError("choose_count requires 1 <= count <= distinct options")
            elif (node.node_type == RuleNodeType.CREDIT_THRESHOLD
                    and (isinstance(node.min_credits, bool)
                        or not isinstance(node.min_credits, (int, float))
                        or not math.isfinite(node.min_credits) or node.min_credits <= 0
                        or node.children)):
                raise RuleValidationError(
                    "credit_threshold requires positive finite credits and no children")
        if node.min_grade is not None and not isinstance(node.min_grade, str):
            raise RuleValidationError("minGrade must be a string")
        for child in node.children:
            walk(child, depth + 1)

    walk(root, 0)


def compute_rules_digest(tree_dict: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(tree_dict)).hexdigest()


def validate_rules_tree(
    tree_dict: dict[str, Any], raw_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Check clause coverage and exact bound evidence for a reviewed release."""
    root = RuleNode.from_dict(tree_dict)
    validate_rule_structure(root)
    raw_by_position = {it["position"]: it for it in raw_items}
    covered: set[int] = set()
    counts = {d.value: 0 for d in CoverageDisposition}
    counts["totalNodes"] = 0
    nodes = []

    def walk(node):
        nodes.append(node)
        counts["totalNodes"] += 1
        counts[node.coverage_disposition.value] += 1
        if node.raw_position is not None:
            if node.raw_position not in raw_by_position:
                raise RuleValidationError("rawPosition does not identify a bound requirement")
            covered.add(node.raw_position)
        for child in node.children:
            walk(child)

    walk(root)
    missing = sorted(set(raw_by_position) - covered)
    if missing:
        raise RuleValidationError(f"Missing raw positions: {missing}; every clause needs coverage")
    def check_evidence(node, inherited=False):
        grounded = inherited
        if node.coverage_disposition != CoverageDisposition.SUPPORTED:
            return
        if node.raw_position is not None:
            available = raw_by_position[node.raw_position].get("evidence") or []
            if not node.evidence or any(e not in available for e in node.evidence):
                raise RuleValidationError("supported clauses require their exact bound source evidence")
            grounded = True
        if not node.children and not grounded:
            raise RuleValidationError("executable requirements require a bound source clause")
        for child in node.children:
            check_evidence(child, grounded)

    check_evidence(root)
    return root.to_dict(), counts


def generate_default_rules_tree(program: dict[str, Any], raw_items: list[dict[str, Any]]) -> dict:
    """Prepare a review draft, retaining mentions without guessing clause semantics."""
    children = tuple(RuleNode(
        node_id=f"pos-{item['position']}", node_type=RuleNodeType.UNRESOLVED,
        title=item.get("text", "").strip() or "Unreviewed requirement",
        description=item.get("text", ""), section=item.get("section"),
        raw_position=item["position"], coverage_disposition=CoverageDisposition.UNRESOLVED,
        course_codes=tuple(item.get("mentions") or ()), count=item.get("choose"),
        evidence=tuple(item.get("evidence") or ()),
        unresolved_reason="Review the full clause and its evidence before assigning executable semantics.",
    ) for item in raw_items)
    root = RuleNode(
        node_id="root", node_type=RuleNodeType.ALL_OF if children else RuleNodeType.UNRESOLVED,
        title=f"{program.get('title', 'Program')} Requirements", children=children,
        unresolved_reason=None if children else "No requirement clauses were supplied.",
        coverage_disposition=CoverageDisposition.SUPPORTED if children else CoverageDisposition.UNRESOLVED,
    )
    return root.to_dict()
