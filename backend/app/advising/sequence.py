"""Conservative, deterministic planning from parsed atlas requisites."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.atlas.requisites import (
    AllOf,
    AnyOf,
    Condition,
    CourseRef,
    Req,
    split_requisite_segments,
)

_MAX_COURSES = 100
_MAX_SELECTED = 50
_MAX_COMPLETED = 100
_MAX_REQ_NODES = 2_000
_MAX_REQ_DEPTH = 64
_MAX_CHOICE_GROUPS = 128
_GRADE_RE = re.compile(
    r"\b(?:grade\s+of\s+[A-D][+-]?|[A-D][+-]?\s+or\s+(?:better|higher))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CourseRequirement:
    code: str
    title: str
    parse_status: str
    prerequisites: Req | None = None
    corequisites: Req | None = None
    requisites_raw: str | None = None
    lifecycle: str = "current"


@dataclass(frozen=True)
class SequenceBlocker:
    code: str
    reason: str
    alternatives: tuple[str, ...] = ()
    alternative_groups: tuple[tuple[str, ...], ...] = ()
    alternative_groups_truncated: bool = False


@dataclass(frozen=True)
class SequencePlan:
    stages: tuple[tuple[str, ...], ...]
    blockers: tuple[SequenceBlocker, ...]
    complete: bool


@dataclass(frozen=True)
class _Evaluation:
    ready: bool
    dependencies: frozenset[str] = frozenset()
    blockers: tuple[SequenceBlocker, ...] = ()


class _InvalidRequirement(ValueError):
    """A persisted requisite tree is malformed or unsafe to evaluate."""


def _walk(req: Req) -> tuple[Req, ...]:
    """Return a bounded preorder traversal, rejecting malformed/cyclic trees."""
    seen: set[int] = set()
    output: list[Req] = []
    stack = [(req, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > _MAX_REQ_DEPTH:
            raise _InvalidRequirement("requisite tree exceeds the supported depth")
        identity = id(current)
        if identity in seen:
            raise _InvalidRequirement("requisite tree is cyclic or contains repeated nodes")
        seen.add(identity)
        output.append(current)
        if len(output) > _MAX_REQ_NODES:
            raise _InvalidRequirement("requisite tree exceeds the supported size")
        if isinstance(current, (AllOf, AnyOf)):
            if not isinstance(current.items, tuple) or not current.items:
                raise _InvalidRequirement("compound requisite must have nonempty tuple items")
            stack.extend((item, depth + 1) for item in reversed(current.items))
        elif isinstance(current, CourseRef):
            if not isinstance(current.code, str) or not current.code or current.code != current.code.strip():
                raise _InvalidRequirement("course reference must have a nonempty exact code")
        elif isinstance(current, Condition):
            if not isinstance(current.text, str) or not current.text.strip():
                raise _InvalidRequirement("condition must have nonempty text")
        else:
            raise _InvalidRequirement(f"unsupported requisite node: {type(current).__name__}")
    return tuple(output)


def _required_raw_segments(raw: str | None) -> tuple[tuple[str, str], ...]:
    """Extract requisite text while excluding explicitly recommended material."""
    if not raw or not raw.strip():
        return ()
    return tuple(
        (kind, text)
        for kind, text in split_requisite_segments(raw)
        if kind in {"prerequisite", "corequisite"}
    )


def _course_refs(req: Req) -> tuple[str, ...]:
    return tuple(node.code for node in _walk(req) if isinstance(node, CourseRef))


def _choice_groups(req: Req) -> tuple[tuple[tuple[str, ...], ...], bool]:
    """Describe OR branches as AND groups, retaining unresolved conditions."""
    def expand(node: Req):
        if isinstance(node, CourseRef):
            return ((node.code,),), False
        if isinstance(node, Condition):
            return ((node.text,),), False
        if isinstance(node, AnyOf):
            groups: set[tuple[str, ...]] = set()
            truncated = False
            for child in node.items:
                child_groups, child_truncated = expand(child)
                truncated |= child_truncated
                for group in child_groups:
                    if group not in groups and len(groups) == _MAX_CHOICE_GROUPS:
                        return tuple(sorted(groups)), True
                    groups.add(group)
            return tuple(sorted(groups)), truncated
        if isinstance(node, AllOf):
            groups: set[tuple[str, ...]] = {()}
            truncated = False
            for child in node.items:
                child_groups, child_truncated = expand(child)
                truncated |= child_truncated
                combined: set[tuple[str, ...]] = set()
                overflow = False
                for left in sorted(groups):
                    for right in child_groups:
                        group = left + right
                        if group not in combined and len(combined) == _MAX_CHOICE_GROUPS:
                            overflow = True
                            break
                        combined.add(group)
                    if overflow:
                        break
                groups = combined
                if overflow:
                    truncated = True
            return tuple(sorted(groups)), truncated
        raise TypeError(f"unsupported requisite node: {type(node).__name__}")

    return expand(req)


def _evaluate(
    req: Req | None,
    owner: str,
    *,
    selected: set[str],
    completed: set[str],
    courses: Mapping[str, CourseRequirement],
) -> _Evaluation:
    if req is None:
        return _Evaluation(True)
    if isinstance(req, CourseRef):
        if req.code in completed:
            return _Evaluation(True)
        if req.code not in selected:
            return _Evaluation(False, blockers=(SequenceBlocker(owner, "missing_prerequisite", (req.code,)),))
        target = courses.get(req.code)
        if target is None:
            return _Evaluation(False, blockers=(SequenceBlocker(req.code, "unknown_course"),))
        if target.lifecycle != "current":
            return _Evaluation(False, blockers=(SequenceBlocker(req.code, "retired_prerequisite"),))
        return _Evaluation(True, frozenset((req.code,)))
    if isinstance(req, Condition):
        return _Evaluation(False, blockers=(SequenceBlocker(owner, "unresolved_condition", (req.text,)),))
    if isinstance(req, AllOf):
        results = [
            _evaluate(item, owner, selected=selected, completed=completed, courses=courses)
            for item in req.items
        ]
        blockers = tuple(blocker for result in results for blocker in result.blockers)
        choice_blockers = tuple(
            blocker for blocker in blockers
            if blocker.reason in {"alternative_required", "multiple_alternatives_selected"}
        )
        if choice_blockers:
            groups, truncated = _choice_groups(req)
            options = (tuple(sorted({item for group in groups for item in group}))
                       if not truncated and all(len(group) == 1 for group in groups) else ())
            reason = ("multiple_alternatives_selected"
                      if any(b.reason == "multiple_alternatives_selected" for b in choice_blockers)
                      else "alternative_required")
            blockers = tuple(b for b in blockers if b not in choice_blockers) + (
                SequenceBlocker(owner, reason, options, groups, truncated),
            )
        return _Evaluation(
            all(result.ready for result in results),
            frozenset(code for result in results for code in result.dependencies),
            blockers,
        )
    if isinstance(req, AnyOf):
        if any(_satisfied_by_completed(item, completed) for item in req.items):
            return _Evaluation(True)
        results = [
            _evaluate(item, owner, selected=selected, completed=completed, courses=courses)
            for item in req.items
        ]
        viable = [result for result in results if result.ready]
        if len(viable) == 1:
            return viable[0]
        if len(viable) > 1:
            groups, truncated = _choice_groups(req)
            flat = tuple(sorted({choice for group in groups for choice in group})) \
                if not truncated and all(len(group) == 1 for group in groups) else ()
            return _Evaluation(
                False,
                blockers=(SequenceBlocker(owner, "multiple_alternatives_selected", flat,
                                          groups, truncated),),
            )
        # Keep every viable route visible. Flattening these values would make
        # (A AND B) OR C look like three interchangeable options.
        groups, truncated = _choice_groups(req)
        # Keep the legacy flat field only when every path is a single choice;
        # otherwise it falsely suggests that compound prerequisites are ORs.
        options = (tuple(sorted({choice for group in groups for choice in group}))
                   if not truncated and all(len(group) == 1 for group in groups) else ())
        return _Evaluation(False, blockers=(SequenceBlocker(
            owner, "alternative_required", options, groups, truncated),))
    raise TypeError(f"unsupported requisite node: {type(req).__name__}")


def _satisfied_by_completed(req: Req, completed: set[str]) -> bool:
    if isinstance(req, CourseRef):
        return req.code in completed
    if isinstance(req, Condition):
        return False
    if isinstance(req, AllOf):
        return all(_satisfied_by_completed(item, completed) for item in req.items)
    if isinstance(req, AnyOf):
        return any(_satisfied_by_completed(item, completed) for item in req.items)
    raise TypeError(f"unsupported requisite node: {type(req).__name__}")


def plan_sequence(
    courses: Mapping[str, CourseRequirement],
    selected_codes: Sequence[str],
    completed_codes: Sequence[str] = (),
) -> SequencePlan:
    """Plan safe stages; unresolved facts block affected courses and dependents.

    Course references are exact atlas codes. Only selected, known courses can
    enter stages. Recommendations are intentionally absent from the input tree.
    """
    if len(courses) > _MAX_COURSES or len(selected_codes) > _MAX_SELECTED or len(completed_codes) > _MAX_COMPLETED:
        raise ValueError("sequence input exceeds the supported size")
    selected = set(selected_codes)
    completed = set(completed_codes)
    if len(selected) != len(selected_codes) or len(completed) != len(completed_codes):
        # Duplicate input is harmless but is an avoidable source of ambiguity.
        selected_codes = tuple(sorted(selected))
    selected = set(selected_codes) - completed
    referenced_selected = selected

    blockers: set[SequenceBlocker] = set()
    known_selected = selected.intersection(courses)
    for code in sorted(selected - courses.keys()):
        blockers.add(SequenceBlocker(code, "unknown_course"))

    # Per-course hard blockers and dependency edges. The corequisite edges are
    # collected separately and collapsed into same-stage bundles below.
    prereq_dependencies: dict[str, set[str]] = {code: set() for code in known_selected}
    coreq_dependencies: dict[str, set[str]] = {code: set() for code in known_selected}
    blocked: set[str] = set(selected - courses.keys())
    for code in sorted(known_selected):
        course = courses[code]
        if getattr(course, "lifecycle", "current") != "current":
            blockers.add(SequenceBlocker(code, "course_retired"))
            blocked.add(code)
            continue
        if course.code != code:
            blockers.add(SequenceBlocker(code, "course_code_mismatch"))
            blocked.add(code)
        try:
            if course.requisites_raw is not None and not isinstance(course.requisites_raw, str):
                raise _InvalidRequirement("raw requisites must be text")
            for tree in (course.prerequisites, course.corequisites):
                if tree is not None:
                    _walk(tree)
        except (AttributeError, TypeError, ValueError):
            blockers.add(SequenceBlocker(code, "requirements_invalid"))
            blocked.add(code)
            continue
        if not isinstance(course.parse_status, str) or course.parse_status not in {"none", "parsed"}:
            blockers.add(SequenceBlocker(code, "requirements_unparsed"))
            blocked.add(code)
        elif course.parse_status == "none" and (course.prerequisites or course.corequisites):
            blockers.add(SequenceBlocker(code, "requirements_invalid"))
            blocked.add(code)
        required_raw = _required_raw_segments(course.requisites_raw)
        if course.parse_status == "none" and required_raw:
            blockers.add(SequenceBlocker(code, "requirements_invalid"))
            blocked.add(code)
        if course.parse_status == "parsed":
            parsed_by_kind = {
                "prerequisite": course.prerequisites,
                "corequisite": course.corequisites,
            }
            missing_raw_trees = {
                kind for kind, _text in required_raw if parsed_by_kind[kind] is None
            }
            if missing_raw_trees:
                blockers.add(SequenceBlocker(code, "requirements_invalid", tuple(sorted(missing_raw_trees))))
                blocked.add(code)
            required_raw_text = " ".join(text for _kind, text in required_raw)
            if _GRADE_RE.search(required_raw_text):
                blockers.add(SequenceBlocker(code, "unresolved_condition", ("grade requirement",)))
                blocked.add(code)
            prereq = _evaluate(
                course.prerequisites,
                code,
                selected=referenced_selected,
                completed=completed,
                courses=courses,
            )
            coreq = _evaluate(
                course.corequisites,
                code,
                selected=referenced_selected,
                completed=completed,
                courses=courses,
            )
            prereq_dependencies[code].update(prereq.dependencies)
            coreq_dependencies[code].update(coreq.dependencies)
            for result, is_coreq in ((prereq, False), (coreq, True)):
                if not result.ready:
                    if is_coreq:
                        blockers.update(
                            SequenceBlocker(
                                blocker.code,
                                "missing_corequisite" if blocker.reason == "missing_prerequisite" else blocker.reason,
                                blocker.alternatives,
                                blocker.alternative_groups,
                                blocker.alternative_groups_truncated,
                            )
                            for blocker in result.blockers
                        )
                    else:
                        blockers.update(result.blockers)
                    blocked.add(code)

    # Same-stage corequisite bundles use union-find over explicitly selected
    # corequisite courses. Already completed corequisites need no bundle.
    parent = {code: code for code in known_selected}

    def find(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for code, dependencies in coreq_dependencies.items():
        for dependency in dependencies:
            if dependency in known_selected:
                union(code, dependency)

    bundles: dict[str, set[str]] = {}
    for code in known_selected:
        bundles.setdefault(find(code), set()).add(code)

    bundle_edges: dict[str, set[str]] = {root: set() for root in bundles}
    for code, dependencies in prereq_dependencies.items():
        root = find(code)
        for dependency in dependencies:
            if dependency not in known_selected:
                continue
            dep_root = find(dependency)
            if root == dep_root:
                blockers.add(SequenceBlocker(code, "prerequisite_cycle", tuple(sorted(bundles[root]))))
                blocked.update(bundles[root])
            else:
                bundle_edges[root].add(dep_root)

    # Find all nodes participating in a prerequisite cycle (including self
    # edges) before propagating blocks to downstream selections.
    visiting: list[str] = []
    state: dict[str, int] = {}
    cyclic: set[str] = set()

    def visit(node: str) -> None:
        state[node] = 1
        visiting.append(node)
        for dependency in sorted(bundle_edges[node]):
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                cyclic.update(visiting[visiting.index(dependency) :])
        visiting.pop()
        state[node] = 2

    for root in sorted(bundle_edges):
        if state.get(root, 0) == 0:
            visit(root)
    for root in sorted(cyclic):
        alternatives = tuple(sorted(bundles[root]))
        for code in alternatives:
            blockers.add(SequenceBlocker(code, "prerequisite_cycle", alternatives))
        blocked.update(alternatives)

    # Any bundle with one blocked member is unsafe as a concurrent stage.
    changed = True
    while changed:
        changed = False
        blocked_roots = {find(code) for code in blocked if code in known_selected}
        for root in blocked_roots:
            for code in bundles[root]:
                if code not in blocked:
                    blocked.add(code)
                    blockers.add(SequenceBlocker(code, "blocked_corequisite"))
                    changed = True
        for root, dependencies in bundle_edges.items():
            if root in blocked_roots:
                continue
            if any(dependency in blocked_roots for dependency in dependencies):
                for code in bundles[root]:
                    if code not in blocked:
                        blocked.add(code)
                        blockers.add(SequenceBlocker(code, "blocked_prerequisite"))
                        changed = True

    # Topological ordering with a sorted frontier gives stable stage order.
    eligible_roots = set(bundles) - {find(code) for code in blocked if code in known_selected}
    remaining = {
        root: {dep for dep in bundle_edges[root] if dep in eligible_roots}
        for root in eligible_roots
    }
    stages: list[tuple[str, ...]] = []
    while remaining:
        ready = sorted(root for root, deps in remaining.items() if not deps)
        if not ready:  # Defensive; cycle detection above should have caught it.
            break
        stage = tuple(sorted(code for root in ready for code in bundles[root]))
        stages.append(stage)
        for root in ready:
            del remaining[root]
        for deps in remaining.values():
            deps.difference_update(ready)

    ordered_blockers = tuple(sorted(blockers, key=lambda item: (item.code, item.reason, item.alternatives)))
    return SequencePlan(tuple(stages), ordered_blockers, not ordered_blockers)
