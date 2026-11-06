"""Tests for deterministic degree audit engine, service, and routes (Item 2)."""

from types import SimpleNamespace

import pytest

from app.advising.audit_engine import DeterministicAuditEngine, audit_reported_courses
from app.advising.comparison import match_reported_courses
from app.programs.rules import CoverageDisposition, RuleNode, RuleNodeType


@pytest.mark.parametrize("grade,status,verified,expected", [
    ("D", "completed", True, "outstanding"),
    ("B", "completed", True, "satisfied"),
    ("B", "completed", False, "conditional_satisfied"),
    ("IP", "in_progress", False, "in_progress_satisfied"),
    (None, "planned", False, "conditional_satisfied"),
    ("P", "completed", True, "unresolved"),
    (None, "completed", True, "unresolved"),
])
def test_engine_exact_course_and_grade_threshold(grade, status, verified, expected):
    rule = RuleNode("req1", RuleNodeType.EXACT_COURSE, "Intro to CS",
                    course_codes=("CS 170",), min_grade="C")
    attempt = {
        "attempt_id": "att1", "submitted_course_code": "CS 170", "grade": grade,
        "status": status, "attempted_credits": 4.0,
        "earned_credits": 4.0 if status == "completed" else 0.0, "is_verified": verified,
    }
    result = DeterministicAuditEngine(rule, [attempt]).run()
    assert result.overall_status == expected
    if expected in {"conditional_satisfied", "in_progress_satisfied"}:
        assert result.assumptions
    if expected in {"outstanding", "unresolved"}:
        assert result.blockers


def test_engine_non_greedy_allocation_and_no_double_count():
    # Tree: ALL_OF [ Req 1: EXACT CS 170, Req 2: CHOOSE 1 from (CS 170, CS 255) ]
    tree = RuleNode(
        node_id="root",
        node_type=RuleNodeType.ALL_OF,
        title="CS Core",
        children=(
            RuleNode(
                node_id="r1",
                node_type=RuleNodeType.EXACT_COURSE,
                title="CS 170 Req",
                course_codes=("CS 170",),
            ),
            RuleNode(
                node_id="r2",
                node_type=RuleNodeType.CHOOSE_COUNT,
                title="Elective Req",
                course_codes=("CS 170", "CS 255"),
                count=1,
            ),
        ),
    )

    # Case A: Student has CS 170 and CS 255.
    # Non-greedy search must allocate CS 170 to r1 and CS 255 to r2, satisfying both!
    attempts = [
        {"attempt_id": "a1", "submitted_course_code": "CS 170", "status": "completed", "grade": "A", "is_verified": True, "attempted_credits": 4.0, "earned_credits": 4.0},
        {"attempt_id": "a2", "submitted_course_code": "CS 255", "status": "completed", "grade": "A", "is_verified": True, "attempted_credits": 3.0, "earned_credits": 3.0},
    ]
    res = DeterministicAuditEngine(tree, attempts).run()
    assert res.overall_status == "satisfied"
    assert res.completed_credits == 7.0

    # Case B: Student has ONLY CS 170.
    # Cannot double count CS 170 for both r1 and r2!
    attempts_single = [
        {"attempt_id": "a1", "submitted_course_code": "CS 170", "status": "completed", "grade": "A", "is_verified": True, "attempted_credits": 4.0, "earned_credits": 4.0},
    ]
    res_single = DeterministicAuditEngine(tree, attempts_single).run()
    assert res_single.overall_status == "outstanding"
    assert res_single.completed_credits == 4.0


def test_engine_repeats_and_unresolved_zero_false_satisfaction():
    # 1. Repeats
    tree = RuleNode(
        node_id="root",
        node_type=RuleNodeType.EXACT_COURSE,
        title="Intro",
        course_codes=("CS 170",),
    )
    # Student failed with F in Fall 2023, then passed with A in Spring 2024
    attempts_repeat = [
        {"attempt_id": "a_f", "submitted_course_code": "CS 170", "term": "Fall 2023", "grade": "F", "status": "failed", "attempted_credits": 4.0, "earned_credits": 0.0},
        {"attempt_id": "a_pass", "submitted_course_code": "CS 170", "term": "Spring 2024", "grade": "A", "status": "completed", "attempted_credits": 4.0, "earned_credits": 4.0, "is_verified": True},
    ]
    res_rep = DeterministicAuditEngine(tree, attempts_repeat).run()
    assert res_rep.overall_status == "satisfied"
    assert res_rep.completed_credits == 4.0

    # 2. Unresolved rule node prevents overall satisfaction!
    tree_with_unresolved = RuleNode(
        node_id="root",
        node_type=RuleNodeType.ALL_OF,
        title="Major",
        children=(
            RuleNode(
                node_id="c1",
                node_type=RuleNodeType.EXACT_COURSE,
                title="Intro",
                course_codes=("CS 170",),
            ),
            RuleNode(
                node_id="c2",
                node_type=RuleNodeType.UNRESOLVED,
                title="Ambiguous Policy",
                coverage_disposition=CoverageDisposition.UNRESOLVED,
                unresolved_reason="Policy wording requires faculty review",
            ),
        ),
    )
    res_unres = DeterministicAuditEngine(tree_with_unresolved, attempts_repeat).run()
    # MUST be unresolved! Never satisfied when a required node is unresolved.
    assert res_unres.overall_status == "unresolved"
    assert any("Ambiguous Policy" in b for b in res_unres.blockers)

    # 3. An unresolved draft root still reports one row per clause, not one row
    #    for the whole program: the reviewer needs the clauses.
    draft = RuleNode(
        node_id="root",
        node_type=RuleNodeType.ALL_OF,
        title="Draft program",
        coverage_disposition=CoverageDisposition.UNRESOLVED,
        unresolved_reason="No requirement clause has reviewed semantics yet.",
        children=tuple(
            RuleNode(
                node_id=f"pos-{index}",
                node_type=RuleNodeType.UNRESOLVED,
                title=title,
                coverage_disposition=CoverageDisposition.UNRESOLVED,
                unresolved_reason="Review the full clause.",
            )
            for index, title in enumerate(("Math 111", "CS 171", "One natural science lab"))
        ),
    )
    res_draft = DeterministicAuditEngine(draft, []).run()
    assert res_draft.overall_status == "unresolved"
    clause_rows = [row for row in res_draft.rule_evaluations if row.parent_node_id == "root"]
    assert [row.title for row in clause_rows] == [
        "Math 111", "CS 171", "One natural science lab",
    ]
    assert all(row.status == "unresolved" for row in clause_rows)

    # 4. ...and stays unresolved when the student has every course its clauses
    #    name: an unreviewed container never counts toward satisfaction.
    for node_type in (RuleNodeType.ALL_OF, RuleNodeType.UNRESOLVED):
        reviewed_clauses = RuleNode(
            node_id="root",
            node_type=node_type,
            title="Unreviewed group",
            coverage_disposition=CoverageDisposition.UNRESOLVED,
            unresolved_reason="Group semantics need review.",
            children=(RuleNode("c1", RuleNodeType.EXACT_COURSE, "Intro", course_codes=("CS 170",)),),
        )
        res_group = DeterministicAuditEngine(reviewed_clauses, attempts_repeat).run()
        assert res_group.overall_status == "unresolved"
        assert res_group.completed_credits == 0.0
        assert [row.title for row in res_group.rule_evaluations] == ["Unreviewed group", "Intro"]


@pytest.mark.parametrize("case,expected,credits", [
    ("empty_all_of", "outstanding", 0),
    ("all_of_courses", "satisfied", 6),
    ("five_credit_courses", "satisfied", 15),
    ("zero_credit", "satisfied", 0),
    ("repeated_course", "outstanding", 3),
    ("nested_alternative", "satisfied", 6),
    ("unused_unknown_alternative", "satisfied", 3),
    ("search_limit", "unresolved", 0),
    ("many_required", "satisfied", 75),
    ("informational_only", "unresolved", 0),
    ("repeated_status", "unresolved", 3),
])
def test_engine_supported_allocation_boundaries(case, expected, credits):
    codes = ["CS 170", "CS 171", "CS 224", "CS 255", "CS 370"]
    if case == "many_required":
        codes = [f"CS {100 + i}" for i in range(25)]
    attempts = [{
        "attempt_id": f"attempt-{i}", "submitted_course_code": code,
        "status": "completed", "grade": "A", "attempted_credits": 3,
        "earned_credits": 3, "is_verified": True,
    } for i, code in enumerate(codes)]
    exact = RuleNode("exact", RuleNodeType.EXACT_COURSE, "CS 170", course_codes=(codes[0],))
    rule = RuleNode("all", RuleNodeType.ALL_OF, "Both courses", course_codes=tuple(codes[:2]))
    limit = 50_000
    if case == "informational_only":
        rule = RuleNode("root", RuleNodeType.ALL_OF, "Unreviewed program", children=(
            RuleNode("note", RuleNodeType.UNRESOLVED, "Header only",
                     coverage_disposition=CoverageDisposition.INFORMATIONAL),
        ))
    elif case == "repeated_status":
        rule, attempts = exact, [{**attempts[0], "status": "repeated"}]
    elif case == "many_required":
        rule = RuleNode("program", RuleNodeType.ALL_OF, "25 requirements", children=tuple(
            RuleNode(f"req-{i}", RuleNodeType.EXACT_COURSE, code, course_codes=(code,))
            for i, code in enumerate(codes)
        ))
    elif case == "empty_all_of":
        attempts = []
    elif case == "five_credit_courses":
        rule = RuleNode("credits", RuleNodeType.CREDIT_THRESHOLD, "15 credits", min_credits=15)
    elif case == "zero_credit":
        rule, attempts = exact, [{**attempts[0], "attempted_credits": 0, "earned_credits": 0}]
    elif case == "repeated_course":
        rule = RuleNode("choose", RuleNodeType.CHOOSE_COUNT, "Two courses",
                        count=2, course_codes=tuple(codes[:2]))
        attempts = [attempts[0], {**attempts[0], "attempt_id": "repeat", "grade": "B"}]
    elif case == "nested_alternative":
        alternative = RuleNode("alt", RuleNodeType.ANY_OF, "Choose one", children=(
            RuleNode("option-a", RuleNodeType.EXACT_COURSE, "A", course_codes=(codes[0],)),
            RuleNode("option-b", RuleNodeType.EXACT_COURSE, "B", course_codes=(codes[1],)),
        ))
        rule = RuleNode("root", RuleNodeType.ALL_OF, "Program", children=(alternative, exact))
    elif case == "unused_unknown_alternative":
        rule = RuleNode("root", RuleNodeType.ANY_OF, "Alternative", children=(
            RuleNode("unknown", RuleNodeType.UNRESOLVED, "Unknown option",
                     unresolved_reason="Approval policy unknown"), exact,
        ))
    elif case == "search_limit":
        rule, limit = exact, 1
    result = DeterministicAuditEngine(rule, attempts, max_search_states=limit).run()
    assert result.overall_status == expected
    assert result.completed_credits == credits
    # Input ordering cannot choose a different allocation or different conclusion.
    reversed_result = DeterministicAuditEngine(
        rule, list(reversed(attempts)), max_search_states=limit,
    ).run()
    assert result == reversed_result
    if case == "unused_unknown_alternative":
        assert not result.blockers
    if case == "search_limit":
        assert result.results_summary["searchLimitReached"]


_CS_170 = {"nodeId": "r", "nodeType": "exact_course", "title": "CS 170", "courseCodes": ["CS 170"]}


@pytest.mark.parametrize("rule,reported,expected", [
    (_CS_170, ["CS 170"], "conditional_satisfied"),
    (_CS_170, [], "outstanding"),
    (_CS_170, ["CS_OX 170"], "conditional_satisfied"),
    ({**_CS_170, "minGrade": "C"}, ["CS 170"], "unresolved"),
    ({"nodeId": "r", "nodeType": "credit_threshold", "title": "Hours", "minCredits": 12.0},
     ["CS 170", "CS 171"], "unresolved"),
    ({"nodeId": "r", "nodeType": "choose_count", "title": "Two", "count": 2,
      "courseCodes": ["CS 170", "CS 171"]}, ["CS 170", "CS_OX 170"], "outstanding"),
], ids=["named", "missing", "other_campus", "grade_floor", "credit_rule", "campus_not_two"])
def test_reported_courses_are_never_verified_passes(rule, reported, expected):
    equivalents = {"CS_OX 170": frozenset({"CS 170"}), "CS 170": frozenset({"CS_OX 170"})}
    assert audit_reported_courses(
        rule, reported, equivalents=equivalents).overall_status == expected


@pytest.mark.parametrize("codes,choose,completed,state,unmatched", [
    (("CS 170",), None, ["CS 170"], "all_named_reported", []),
    (("CS 370", "CS 371W"), 1, ["CS 370"], "all_named_reported", []),
    (("CS 334", "CS 377"), None, ["CS 334"], "some_named_reported", []),
    (("CS 170",), None, ["CS_OX 170"], "all_named_reported", []),
    (("CS 170",), None, ["MATH 111"], "none_reported", ["MATH 111"]),
    ((), None, ["CS 170"], "no_course_named", ["CS 170"]),
], ids=["named", "choose_one", "maybe_alternatives", "other_campus", "absent", "no_code"])
def test_reported_courses_match_lines_without_claiming_them_met(
        codes, choose, completed, state, unmatched):
    line = SimpleNamespace(mentioned_course_codes=codes, choose=choose)
    matches, leftover = match_reported_courses(
        [line], completed, {"CS_OX 170": frozenset({"CS 170"})})
    assert (matches[0].state, list(leftover)) == (state, unmatched)
