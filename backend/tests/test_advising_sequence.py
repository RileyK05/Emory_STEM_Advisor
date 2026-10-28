import pytest

from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, parse_requisites


def course(code, *, prereq=None, coreq=None, status="none", raw=None):
    return CourseRequirement(code, f"Title {code}", status, prereq, coreq, raw)


def test_chain_and_requirements_create_stable_stages():
    courses = {
        "MATH 111": course("MATH 111"),
        "CS 170": course("CS 170", prereq=CourseRef("MATH 111"), status="parsed"),
        "CS 253": course(
            "CS 253",
            prereq=AllOf((CourseRef("CS 170"), CourseRef("MATH 111"))),
            status="parsed",
        ),
    }

    plan = plan_sequence(courses, ["CS 253", "CS 170", "MATH 111"])

    assert plan.stages == (("MATH 111",), ("CS 170",), ("CS 253",))
    assert plan.blockers == ()
    assert plan.complete


@pytest.mark.parametrize("raw,completed,ready", [
    ("MATH 111", [], False),
    ("MATH 111", ["MATH 111"], True),
    ("(CS 170 or CS_OX 170) and (MATH 111 or MATH_OX 111) or equivalent transfer credit as prerequisites",
     ["CS_OX 170", "MATH_OX 111"], True),
    ("(CS 170 or CS_OX 170) and (MATH 111 or MATH_OX 111) or equivalent transfer credit as prerequisites",
     ["CS 170", "MATH 111"], True),
    ("(CS 170 or CS_OX 170) and (MATH 111 or MATH_OX 111) or equivalent transfer credit as prerequisites",
     ["CS 170"], False),
    ("(CS 170 or CS_OX 170) and (MATH 111 or MATH_OX 111) and equivalent transfer credit as prerequisites",
     ["CS 170", "MATH 111"], False),
    ("CS 170 or equivalent transfer credit", ["CS 170"], True),
    ("CS 170 and equivalent transfer credit", ["CS 170"], False),
    ("CS 170 and permission of instructor", ["CS 170"], False),
])
def test_unselected_prerequisite_blocks_course_but_completed_discharges_it(raw, completed, ready):
    parsed = parse_requisites(raw, course_code="CS 224")
    courses = {"CS 224": course("CS 224", prereq=parsed.trees.get("prerequisite"),
                               status=parsed.status, raw=raw)}
    plan = plan_sequence(courses, ["CS 224"], completed)
    assert plan.complete is ready
    assert plan.stages == ((("CS 224",),) if ready else ())
    assert bool(plan.blockers) is not ready


def test_or_requires_one_explicit_choice_and_never_guesses():
    courses = {
        "MATH 111": course("MATH 111"),
        "CS 170": course("CS 170"),
        "CS 253": course(
            "CS 253", prereq=AnyOf((CourseRef("MATH 111"), CourseRef("CS 170"))), status="parsed"
        ),
    }

    unresolved = plan_sequence(courses, ["CS 253"])
    chosen = plan_sequence(courses, ["CS 253", "MATH 111"])
    already_done = plan_sequence(courses, ["CS 253"], ["CS 170"])

    assert unresolved.stages == ()
    assert any(b.reason == "alternative_required" and b.alternatives == ("CS 170", "MATH 111") for b in unresolved.blockers)
    assert chosen.stages == (("MATH 111",), ("CS 253",))
    assert already_done.stages == (("CS 253",),)


def test_or_discloses_condition_as_an_alternative_without_requiring_it():
    courses = {
        "CS 253": course(
            "CS 253",
            prereq=AnyOf((CourseRef("CS 170"), Condition("equivalent transfer credit"))),
            status="parsed",
        ),
    }

    unresolved = plan_sequence(courses, ["CS 253"])
    completed = plan_sequence(courses, ["CS 253"], ["CS 170"])

    assert not unresolved.complete
    assert any(b.reason == "alternative_required" and
               set(b.alternatives) == {"CS 170", "equivalent transfer credit"}
               for b in unresolved.blockers)
    assert completed.complete and not completed.blockers


def test_or_blocker_preserves_and_groups_and_transfer_condition_branch():
    courses = {
        "CS 253": course(
            "CS 253",
            prereq=AnyOf((AllOf((CourseRef("CS 253A"), CourseRef("CS 255"))),
                          Condition("equivalent transfer credit"))),
            status="parsed",
        ),
        "CS 253A": course("CS 253A"),
        "CS 255": course("CS 255"),
    }
    plan = plan_sequence(courses, ["CS 253"])
    blocker = next(b for b in plan.blockers if b.reason == "alternative_required")
    assert blocker.alternative_groups == (("CS 253A", "CS 255"), ("equivalent transfer credit",))
    assert blocker.alternatives == ()


def test_partially_completed_and_or_branch_keeps_remaining_requirements_and_other_route():
    courses = {
        "CS 253": course(
            "CS 253",
            prereq=AnyOf((AllOf((CourseRef("CS 253A"), CourseRef("CS 255"))),
                          Condition("equivalent transfer credit"))),
            status="parsed",
        ),
        "CS 253A": course("CS 253A"),
        "CS 255": course("CS 255"),
    }
    plan = plan_sequence(courses, ["CS 253"], ["CS 253A"])
    blocker = next(b for b in plan.blockers if b.reason == "alternative_required")
    assert blocker.alternative_groups == (("CS 253A", "CS 255"), ("equivalent transfer credit",))
    assert blocker.alternatives == ()
    assert "CS 255" in blocker.alternative_groups[0]


def test_large_or_group_report_is_explicitly_truncated():
    alternatives = tuple(CourseRef(f"X {index}") for index in range(140))
    courses = {"TARGET 1": course("TARGET 1", prereq=AnyOf(alternatives), status="parsed")}
    plan = plan_sequence(courses, ["TARGET 1"])
    blocker = next(b for b in plan.blockers if b.reason == "alternative_required")
    assert len(blocker.alternative_groups) == 128
    assert blocker.alternative_groups_truncated
    assert all(group != ("Additional requisite alternatives omitted; consult the cited requirement.",)
               for group in blocker.alternative_groups)


def test_truncated_or_under_and_still_includes_later_mandatory_terms():
    many_choices = AnyOf(tuple(CourseRef(f"X {index}") for index in range(140)))
    prereq = AllOf((many_choices, CourseRef("MATH 111")))
    plan = plan_sequence({"TARGET 1": course("TARGET 1", prereq=prereq, status="parsed")},
                         ["TARGET 1"])
    blocker = next(b for b in plan.blockers if b.reason == "alternative_required")
    assert blocker.alternatives == ()
    assert blocker.alternative_groups_truncated is True
    assert blocker.alternative_groups
    assert all("MATH 111" in group for group in blocker.alternative_groups)


def test_wide_and_group_is_processed_without_recursing_per_child():
    prereq = AllOf(tuple(CourseRef(f"X {index}") for index in range(1500)))
    plan = plan_sequence({"TARGET 1": course("TARGET 1", prereq=prereq, status="parsed")},
                         ["TARGET 1"])
    blocker = next(b for b in plan.blockers if b.reason == "missing_prerequisite")
    assert blocker.alternatives == ("X 0",)


def test_unknown_course_and_unparsed_requirements_block_without_losing_safe_course():
    courses = {
        "CS 253": course("CS 253", prereq=CourseRef("MATH 111"), status="parsed"),
        "BIO 100": course("BIO 100", status="ambiguous"),
        "HIST 101": course("HIST 101"),
    }

    plan = plan_sequence(courses, ["CS 253", "BIO 100", "HIST 101", "FAKE 999"])

    assert plan.stages == (("HIST 101",),)
    assert {(b.code, b.reason) for b in plan.blockers} >= {
        ("FAKE 999", "unknown_course"),
        ("BIO 100", "requirements_unparsed"),
        ("CS 253", "missing_prerequisite"),
    }


def test_conditions_and_raw_grade_requirements_remain_unresolved():
    courses = {
        "CS 300": course("CS 300", prereq=Condition("junior standing"), status="parsed"),
        "CS 301": course("CS 301", status="parsed", raw="Prerequisite: CS 300 with a grade of C or better"),
        "BIO 100": course("BIO 100"),
    }

    plan = plan_sequence(courses, ["CS 300", "CS 301", "BIO 100"])

    assert plan.stages == (("BIO 100",),)
    assert {(b.code, b.reason) for b in plan.blockers} >= {
        ("CS 300", "unresolved_condition"),
        ("CS 301", "unresolved_condition"),
    }


def test_cycles_block_cycle_members_and_dependents_but_keep_independent_courses():
    courses = {
        "A 100": course("A 100", prereq=CourseRef("B 100"), status="parsed"),
        "B 100": course("B 100", prereq=CourseRef("A 100"), status="parsed"),
        "C 100": course("C 100", prereq=CourseRef("A 100"), status="parsed"),
        "D 100": course("D 100"),
    }

    plan = plan_sequence(courses, ["A 100", "B 100", "C 100", "D 100"])

    assert plan.stages == (("D 100",),)
    assert sum(b.reason == "prerequisite_cycle" for b in plan.blockers) == 2
    assert any(b.code == "C 100" and b.reason == "blocked_prerequisite" for b in plan.blockers)


def test_corequisites_are_same_stage_and_corequisite_must_be_selected_or_completed():
    courses = {
        "BIO 100": course("BIO 100", coreq=CourseRef("CHEM 100"), status="parsed"),
        "CHEM 100": course("CHEM 100"),
        "MATH 100": course("MATH 100"),
    }

    together = plan_sequence(courses, ["BIO 100", "CHEM 100", "MATH 100"])
    absent = plan_sequence(courses, ["BIO 100", "MATH 100"])
    completed = plan_sequence(courses, ["BIO 100"], ["CHEM 100"])

    assert together.stages == (("BIO 100", "CHEM 100", "MATH 100"),)
    assert absent.stages == (("MATH 100",),)
    assert any(b.reason == "missing_corequisite" for b in absent.blockers)
    assert completed.stages == (("BIO 100",),)


def test_dependents_of_a_blocked_corequisite_bundle_are_not_planned():
    courses = {
        "BIO 100": course("BIO 100", coreq=CourseRef("CHEM 100"), status="parsed"),
        "CHEM 100": course("CHEM 100", prereq=Condition("permission"), status="parsed"),
        "BIO 200": course("BIO 200", prereq=CourseRef("BIO 100"), status="parsed"),
    }

    plan = plan_sequence(courses, ["BIO 100", "CHEM 100", "BIO 200"])

    assert plan.stages == ()
    assert any(b.code == "BIO 200" and b.reason == "blocked_prerequisite" for b in plan.blockers)
    assert any(b.code == "BIO 100" and b.reason == "blocked_corequisite" for b in plan.blockers)


def test_malformed_tree_is_a_structured_blocker_instead_of_an_exception():
    deep = CourseRef("MATH 111")
    for _ in range(70):
        deep = AllOf((deep,))
    courses = {
        "CS 253": course("CS 253", prereq=deep, status="parsed"),
        "BIO 100": course("BIO 100", prereq=AllOf(()), status="parsed"),
        "CHEM 100": course("CHEM 100", prereq=CourseRef("  "), status="parsed"),
        "PHYS 100": course("PHYS 100", prereq=Condition("  "), status="parsed"),
        "MATH 100": course("MATH 100"),
    }

    plan = plan_sequence(courses, list(courses))

    assert plan.stages == (("MATH 100",),)
    assert {(b.code, b.reason) for b in plan.blockers} >= {
        ("CS 253", "requirements_invalid"),
        ("BIO 100", "requirements_invalid"),
        ("CHEM 100", "requirements_invalid"),
        ("PHYS 100", "requirements_invalid"),
    }


def test_parse_status_and_raw_required_text_must_agree_but_recommendations_are_ignored():
    courses = {
        "CS 253": course("CS 253", status="parsed", raw="Prerequisite: MATH 111"),
        "BIO 100": course("BIO 100", status="none", raw="Corequisite: CHEM 100"),
        "HIST 101": course(
            "HIST 101", status="parsed", raw="Recommended preparation: MATH 111"
        ),
        "ART 101": course("ART 101", status="none", raw="Recommended: prior experience"),
        "MATH 100": course("MATH 100"),
    }

    plan = plan_sequence(courses, list(courses))

    assert plan.stages == (("ART 101", "HIST 101", "MATH 100"),)
    assert {(b.code, b.reason) for b in plan.blockers} >= {
        ("CS 253", "requirements_invalid"),
        ("BIO 100", "requirements_invalid"),
    }


def test_input_size_is_bounded():
    try:
        plan_sequence({}, [f"X {index}" for index in range(201)])
    except ValueError as exc:
        assert "size" in str(exc)
    else:
        raise AssertionError("oversized input should fail")
