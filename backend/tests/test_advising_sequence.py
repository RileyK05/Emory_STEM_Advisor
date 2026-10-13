from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef


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


def test_unselected_prerequisite_blocks_course_but_completed_discharges_it():
    courses = {"CS 170": course("CS 170", prereq=CourseRef("MATH 111"), status="parsed")}

    missing = plan_sequence(courses, ["CS 170"])
    completed = plan_sequence(courses, ["CS 170"], ["MATH 111"])

    assert missing.stages == ()
    assert ("CS 170", "missing_prerequisite", ("MATH 111",)) in {
        (b.code, b.reason, b.alternatives) for b in missing.blockers
    }
    assert completed.stages == (("CS 170",),)
    assert completed.complete


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
