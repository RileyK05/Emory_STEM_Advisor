"""Conservative, deterministic planning from parsed atlas requisites."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.atlas.records import normalize_code
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
    # (course, prerequisite) pairs among selected courses. The stages give the
    # order; a term packer needs the exact edge to overlap adjacent stages.
    prerequisites: tuple[tuple[str, str], ...] = ()
    # Selected courses that must be taken in the same term (lecture + lab).
    corequisites: tuple[tuple[str, ...], ...] = ()


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


def _distinct_courses(
    results: list[_Evaluation],
    campus_equivalents: Mapping[str, frozenset[str]] | None,
) -> list[list[_Evaluation]]:
    """Group evaluated alternatives by the course they name.

    An alternative that resolved to one course is that course. Two
    alternatives that resolved to campus equivalents of one another name the
    same course at two campuses, so they belong to one group. A compound
    alternative names several courses and is never collapsed into another.
    """
    groups: list[list[_Evaluation]] = []
    for result in results:
        for group in groups:
            if _same_course(group[0], result, campus_equivalents):
                group.append(result)
                break
        else:
            groups.append([result])
    return groups


def _same_course(
    left: _Evaluation,
    right: _Evaluation,
    campus_equivalents: Mapping[str, frozenset[str]] | None,
) -> bool:
    """Whether two alternatives resolved to the same course."""
    if len(left.dependencies) != 1 or len(right.dependencies) != 1:
        return False
    left_code = next(iter(left.dependencies))
    right_code = next(iter(right.dependencies))
    if left_code == right_code:
        return True
    if normalize_code(left_code) == normalize_code(right_code):
        return True
    if not campus_equivalents:
        return False
    return right_code in campus_equivalents.get(left_code, ())


def _evaluate(
    req: Req | None,
    owner: str,
    *,
    selected: set[str],
    completed: set[str],
    courses: Mapping[str, CourseRequirement],
    completed_aliases: frozenset[str] = frozenset(),
    selected_aliases: Mapping[str, str] | None = None,
    campus_equivalents: Mapping[str, frozenset[str]] | None = None,
) -> _Evaluation:
    if req is None:
        return _Evaluation(True)
    if isinstance(req, CourseRef):
        canonical = normalize_code(req.code) or req.code
        names = tuple(dict.fromkeys((req.code, canonical)))
        if any(name in completed or name in completed_aliases for name in names):
            return _Evaluation(True)
        # A requirement may name the other campus's offering, or the old subject
        # of the course the student selected (QTM 150 -> DATASCI 150). The
        # student's own course is the one that satisfies it.
        matched = next((name for name in names if name in selected), None)
        if matched is None:
            matched = next(((selected_aliases or {}).get(name) for name in names
                            if (selected_aliases or {}).get(name)), None)
        if matched is None:
            return _Evaluation(False, blockers=(SequenceBlocker(owner, "missing_prerequisite", (req.code,)),))
        target = next((courses.get(name) for name in (matched, req.code, canonical)
                       if courses.get(name) is not None), None)
        if target is None:
            return _Evaluation(False, blockers=(SequenceBlocker(req.code, "unknown_course"),))
        if target.lifecycle != "current":
            return _Evaluation(False, blockers=(SequenceBlocker(req.code, "retired_prerequisite"),))
        return _Evaluation(True, frozenset((matched,)))
    if isinstance(req, Condition):
        return _Evaluation(False, blockers=(SequenceBlocker(owner, "unresolved_condition", (req.text,)),))
    if isinstance(req, AllOf):
        results = [
            _evaluate(item, owner, selected=selected, completed=completed, courses=courses,
                      completed_aliases=completed_aliases, selected_aliases=selected_aliases,
                      campus_equivalents=campus_equivalents)
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
        if any(_satisfied_by_completed(item, completed, completed_aliases) for item in req.items):
            return _Evaluation(True)
        results = [
            _evaluate(item, owner, selected=selected, completed=completed, courses=courses,
                      completed_aliases=completed_aliases, selected_aliases=selected_aliases,
                      campus_equivalents=campus_equivalents)
            for item in req.items
        ]
        viable = [result for result in results if result.ready]
        if len(viable) == 1:
            return viable[0]
        if len(viable) > 1:
            # Two alternatives that are the same course at two campuses are one
            # choice, not a conflict: "MATH 111 or MATH_OX 111" is one course,
            # and taking it at either campus is taking it.
            distinct = _distinct_courses(viable, campus_equivalents)
            if len(distinct) == 1:
                merged = distinct[0]
                return _Evaluation(
                    True,
                    frozenset(code for result in merged for code in result.dependencies),
                    tuple(blocker for result in merged for blocker in result.blockers),
                )
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


def _satisfied_by_completed(
    req: Req, completed: set[str], completed_aliases: frozenset[str] = frozenset()
) -> bool:
    if isinstance(req, CourseRef):
        return req.code in completed or req.code in completed_aliases
    if isinstance(req, Condition):
        return False
    if isinstance(req, AllOf):
        return all(_satisfied_by_completed(item, completed, completed_aliases)
                   for item in req.items)
    if isinstance(req, AnyOf):
        return any(_satisfied_by_completed(item, completed, completed_aliases)
                   for item in req.items)
    raise TypeError(f"unsupported requisite node: {type(req).__name__}")


def plan_sequence(
    courses: Mapping[str, CourseRequirement],
    selected_codes: Sequence[str],
    completed_codes: Sequence[str] = (),
    campus_equivalents: Mapping[str, frozenset[str]] | None = None,
) -> SequencePlan:
    """Plan safe stages; unresolved facts block affected courses and dependents.

    Course references are exact atlas codes. Only selected, known courses can
    enter stages. Recommendations are intentionally absent from the input tree.

    ``campus_equivalents`` maps a course code to the codes of the same course
    at the other campus. A student who took `MATH_OX 111` at Oxford has taken
    what a `MATH 111` requirement asks for, so the requirement is satisfied.
    The student's own plan is untouched: only the satisfaction test knows
    about the equivalence.
    """
    if len(courses) > _MAX_COURSES or len(selected_codes) > _MAX_SELECTED or len(completed_codes) > _MAX_COMPLETED:
        raise ValueError("sequence input exceeds the supported size")
    selected = {normalize_code(code) or code for code in selected_codes}
    completed = {normalize_code(code) or code for code in completed_codes}
    if len(selected) != len(selected_codes) or len(completed) != len(completed_codes):
        # Duplicate input is harmless but is an avoidable source of ambiguity.
        selected_codes = tuple(sorted(selected))
    selected = set(selected_codes) - completed
    referenced_selected = selected
    completed_aliases: frozenset[str] = frozenset()
    selected_aliases: dict[str, str] = {}
    if campus_equivalents:
        completed_aliases = frozenset(
            alias for code in completed
            for alias in campus_equivalents.get(code, ())
            if alias not in completed
        )
        # A requirement may name the other campus's offering of a course the
        # student selected; the student's own course satisfies it.
        for code in selected:
            for alias in campus_equivalents.get(code, ()):
                selected_aliases.setdefault(alias, code)

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
                completed_aliases=completed_aliases,
                selected_aliases=selected_aliases,
                campus_equivalents=campus_equivalents,
            )
            coreq = _evaluate(
                course.corequisites,
                code,
                selected=referenced_selected,
                completed=completed,
                courses=courses,
                completed_aliases=completed_aliases,
                selected_aliases=selected_aliases,
                campus_equivalents=campus_equivalents,
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
    edges = tuple(sorted(
        (code, dependency)
        for code, dependencies in prereq_dependencies.items()
        for dependency in dependencies
        if dependency in known_selected
    ))
    staged = {code for stage in stages for code in stage}
    corequisites = tuple(sorted(
        tuple(sorted(code for code in bundle if code in staged))
        for bundle in bundles.values()
        if len([code for code in bundle if code in staged]) > 1
    ))
    return SequencePlan(tuple(stages), ordered_blockers, not ordered_blockers, edges,
                        corequisites)


@dataclass(frozen=True)
class TermPacking:
    """A deterministic term assignment for a plan's selected courses."""

    terms: tuple[tuple[str, ...], ...]
    unscheduled: tuple[str, ...]
    credits: tuple[float, ...]


def pack_terms(
    plan: SequencePlan,
    *,
    credits: Mapping[str, float] | None = None,
    seasons: Mapping[str, frozenset[str]] | None = None,
    completed: Sequence[str] = (),
    max_credits: float = 16.0,
    max_courses: int = 5,
    max_terms: int = 8,
) -> TermPacking:
    """Pack prerequisite stages into terms with a credit and course cap.

    The stages still decide the order. A course can move into a term as soon as
    its own selected prerequisites are in an earlier term, which lets two
    adjacent stages overlap without ever putting a course before its
    prerequisite. A course's known offered seasons are a preference; when no
    season-matching course can fill a term, a ready course is used anyway so a
    sparse offering record cannot deadlock the plan.
    """
    stage_of = {code: index for index, stage in enumerate(plan.stages) for code in stage}
    if not stage_of:
        return TermPacking((), (), ())
    bundle_of: dict[str, tuple[str, ...]] = {}
    for bundle in plan.corequisites:
        for code in bundle:
            bundle_of[code] = tuple(bundle)
    units = sorted({bundle_of.get(code, (code,)) for code in stage_of},
                   key=lambda unit: (min(stage_of[code] for code in unit), unit))
    unit_of = {code: unit for unit in units for code in unit}
    dependencies = {unit: set() for unit in units}
    for code, dependency in plan.prerequisites:
        if code in unit_of and dependency in unit_of:
            left, right = unit_of[code], unit_of[dependency]
            if left != right:
                dependencies[left].add(right)
    credit_value = credits or {}
    offered = seasons or {}
    done_codes = set(completed)
    done_units: set[tuple[str, ...]] = set()
    remaining = set(units)
    terms: list[tuple[str, ...]] = []
    term_credits: list[float] = []
    cycle = ("Fall", "Spring")

    def credits_of(unit) -> float:
        return sum(float(credit_value.get(code, 3.0)) for code in unit)

    def offered_in(unit, season: str) -> bool:
        return all(_offered_in(code, offered, season) for code in unit)

    for index in range(max(0, max_terms)):
        season = cycle[index % len(cycle)]
        eligible = sorted(
            (unit for unit in remaining
             if dependencies[unit] <= done_units and not set(unit) & done_codes),
            key=lambda unit: (min(stage_of[code] for code in unit), unit),
        )
        chosen: list[tuple[str, ...]] = []
        used = 0.0
        for unit in eligible:
            if not offered_in(unit, season):
                continue
            value = credits_of(unit)
            if len(chosen) + len(unit) > max_courses or used + value > max_credits:
                continue
            chosen.append(unit)
            used += value
        if not chosen:
            # A course offered only in an unmodelled season would otherwise
            # never be scheduled. Season is a preference, not a hard block.
            for unit in eligible:
                value = credits_of(unit)
                if len(chosen) + len(unit) > max_courses or used + value > max_credits:
                    continue
                chosen.append(unit)
                used += value
        if not chosen:
            break
        codes = tuple(sorted(code for unit in chosen for code in unit))
        terms.append(codes)
        term_credits.append(used)
        done_codes.update(codes)
        done_units.update(chosen)
        remaining.difference_update(chosen)
        if not remaining:
            break
    unscheduled = tuple(sorted(code for unit in remaining for code in unit))
    return TermPacking(tuple(terms), unscheduled, tuple(term_credits))


def _offered_in(code: str, seasons: Mapping[str, frozenset[str]], season: str) -> bool:
    known = seasons.get(code)
    return not known or season.casefold() in known
