"""Offline regression for requisite strings collected from the live 2026–27 catalog."""

import json
from collections import Counter
from pathlib import Path

from app.atlas.importer import unresolved_courses
from app.atlas.records import AtlasCourse, normalize_code
from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, parse_requisites

FIXTURE = Path(__file__).parent / "fixtures" / "emory_prerequisites_2026_2027.json"


def test_current_catalog_baseline_is_classified_without_inferred_edges():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["term"] == "2026-2027"
    assert len(data["courses"]) >= 30
    counts = Counter()
    for course in data["courses"]:
        assert course["url"].startswith("https://college.catalog.emory.edu/courses/")
        parsed = parse_requisites(course["requisites"])
        counts[parsed.status] += 1
        assert parsed.status == course["expected_status"]
        # Mentions remain useful for review, but a partial string parse never
        # becomes a boolean prerequisite tree or graph edge.
        assert parsed.mentions
        if parsed.status != "parsed":
            assert parsed.trees == {}
    assert sum(counts.values()) == len(data["courses"])


def test_import_review_path_marks_every_catalog_record_for_human_review():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Titles/descriptions are intentionally absent from this requisite-only
    # audit. The importer review classifier consumes the exact code and raw
    # requisite field; it does not create graph nodes for these records.
    courses = [AtlasCourse(code=row["code"], title="", requisites=row["requisites"])
               for row in data["courses"]]
    unresolved = unresolved_courses(courses)
    expected_unresolved = {
        row["code"] for row in data["courses"]
        if row["expected_status"] != "parsed"
    }
    assert {course.code for course in unresolved} == expected_unresolved


def test_explicit_catalog_or_alternatives_keep_transfer_credit_as_a_condition():
    # These trees are reviewed directly against the fixture's exact raw strings.
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_code = {row["code"]: row for row in data["courses"]}
    assert parse_requisites(by_code["CS 370"]["requisites"]).trees["prerequisite"] == AnyOf((
        CourseRef("CS 253"), Condition("equivalent transfer credit")
    ))
    assert parse_requisites(by_code["BIOL 450"]["requisites"]).trees["prerequisite"] == AnyOf((
        CourseRef("BIOL 360"), CourseRef("NBB 301"), CourseRef("IBS 514"),
        Condition("equivalent transfer credit")
    ))
    assert parse_requisites(by_code["ENVS 250"]["requisites"]).trees["prerequisite"] == AnyOf((
        CourseRef("QTM 100"), CourseRef("QTM_OX 100"), CourseRef("BUS 350"),
        CourseRef("BUS_OX 350"), Condition("equivalent transfer credit")
    ))


def test_transfer_credit_tail_is_a_modifier_but_true_mixed_operators_stay_ambiguous():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_code = {row["code"]: row for row in data["courses"]}
    # EMO-028: "... or equivalent transfer credit" is a modifier, so the mixed
    # and/or is not a genuine ambiguity.
    assert parse_requisites(by_code["CS 350"]["requisites"]).trees["prerequisite"] == AllOf((
        AllOf((CourseRef("CS 253"), CourseRef("CS 255"))),
        Condition("equivalent transfer credit"),
    ))
    # A real unparenthesized mix (no trailing clause to blame) stays ambiguous.
    assert parse_requisites(by_code["MATH 345"]["requisites"]).status == "ambiguous"
    # The run-together conjunction is repaired, but the and/or mix remains.
    assert parse_requisites(by_code["BIOL 142"]["requisites"]).status == "ambiguous"
    # A restriction after the transfer clause is never dropped.
    assert parse_requisites(by_code["CHEM 340"]["requisites"]).status == "unparsed"


def test_catalog_cross_campus_codes_bare_alternates_and_square_brackets_have_explicit_trees():
    # These exact expressions occur as substrings in the live fixture; terminal
    # transfer-credit wording is intentionally excluded here because it changes
    # the outer expression scope.
    assert normalize_code("MATH_OX221") == "MATH_OX 221"
    assert normalize_code("MATH 221") != normalize_code("MATH_OX 221")
    assert parse_requisites("CS 171 or 171Z or CS_OX 171").trees["prerequisite"] == AnyOf((
        CourseRef("CS 171"), CourseRef("CS 171Z"), CourseRef("CS_OX 171")
    ))
    assert parse_requisites(
        "[(BIOL 142 and BIOL 142L) or BIOL_OX 142 or BIOL_OX 142X] "
        "and (QTM 100 or QTM_OX 100)"
    ).trees["prerequisite"] == AllOf((
        AnyOf((
            AllOf((CourseRef("BIOL 142"), CourseRef("BIOL 142L"))),
            CourseRef("BIOL_OX 142"), CourseRef("BIOL_OX 142X"),
        )),
        AnyOf((CourseRef("QTM 100"), CourseRef("QTM_OX 100"))),
    ))
