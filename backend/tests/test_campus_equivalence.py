"""An Oxford course is the other campus's offering of a course.

Emory College and Oxford College publish the same course under different
codes. `MATH 112` is "Calculus II" in Atlanta and `MATH_OX 112` is "Calculus
II" at Oxford. A student who took one has taken the other, and a requirement
that names either accepts either.

The database has to say so. These tests cover the derivation, the rows it
writes, the mentions it resolves, and the planner that has to honour it.
"""

from __future__ import annotations

from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.equivalence import (
    CATALOG_BASIS,
    RECORD_BASIS,
    derive_campus_equivalents,
    is_oxford,
    titles_compatible,
)
from app.atlas.requisites import AllOf, AnyOf, CourseRef


def _reader(text: str):
    from app.atlas.requisites import canonical_codes_in

    return canonical_codes_in(text)


def _derive(courses, texts):
    return derive_campus_equivalents(courses, texts, reader=_reader)


# --- the derivation -------------------------------------------------------

def test_a_requirement_that_offers_both_campuses_states_the_equivalence():
    """Emory's own alternative list is the authority.

    "BIOL 142 or BIOL_OX 142" is how the catalog states that the two codes are
    the same course at two campuses.
    """
    courses = [
        ("BIOL 142", "Foundations of Modern Biology II"),
        ("BIOL 261", "Biology of Insects"),
    ]
    texts = [
        ("BIOL 142 or BIOL_OX 142 or BIOL 240 or equivalent transfer credit "
         "as prerequisite."),
    ]
    pairs = _derive(courses, texts)
    assert ("BIOL_OX 142", "BIOL 142") in {
        (p.oxford_code, p.atlanta_code) for p in pairs}
    assert [p.basis for p in pairs
            if p.oxford_code == "BIOL_OX 142"] == [CATALOG_BASIS]


def test_a_course_the_master_lacks_still_gets_its_equivalence():
    """The catalog names an Oxford course the master has no row for.

    That is the case the relation exists for: the requirement names
    BIOL_OX 142, the master carries only BIOL 142, and the mention would
    otherwise resolve to nothing.
    """
    courses = [
        ("BIOL 142", "Foundations of Modern Biology II"),
        ("BIOL 261", "Biology of Insects"),
    ]
    texts = [
        ("BIOL 142 or BIOL_OX 142 or BIOL 240 or equivalent transfer credit "
         "as prerequisite."),
    ]
    pairs = _derive(courses, texts)
    assert ("BIOL_OX 142", "BIOL 142") in {
        (p.oxford_code, p.atlanta_code) for p in pairs}


def test_a_topic_variant_is_the_course_it_varies() -> None:
    """The suffix names a variant of that one course.

    "BIOL 142 or BIOL_OX 142 or BIOL_OX 142Q or BIOL_OX 142X" publishes
    BIOL_OX 142Q as a variant of BIOL 142, the course the other campuses'
    records give the same number and title for.
    """
    courses = [
        ("BIOL 142", "Foundations of Modern Biology II"),
        ("BIOL 261", "Biology of Insects"),
    ]
    texts = [
        ("BIOL 142 or BIOL_OX 142 or BIOL_OX 142Q or BIOL_OX 142X or BIOL 240 "
         "or equivalent transfer credit as prerequisite."),
    ]
    pairs = _derive(courses, texts)
    derived = {(p.oxford_code, p.atlanta_code) for p in pairs}
    assert ("BIOL_OX 142Q", "BIOL 142") in derived
    assert ("BIOL_OX 142X", "BIOL 142") in derived


def test_a_code_that_is_its_own_course_is_not_a_variant_of_another() -> None:
    """MATH_OX 112Z is MATH 112Z, not a variant of MATH 112.

    The catalog writes "MATH 112 or MATH 112Z or MATH_OX 112 or MATH_OX
    112Z". The two Atlanta courses both strip to "112", so the fallback would
    be ambiguous, and the exact number settles it.
    """
    courses = [
        ("MATH 112", "Calculus II"),
        ("MATH 112Z", "Calculus II"),
        ("MATH 112Z0", "Calculus II"),
        ("MATH 261", "Biology of Insects"),
    ]
    texts = [
        ("MATH 112 or MATH 112Z or MATH_OX 112 or MATH_OX 112Z or MATH 112Z0 "
         "or equivalent transfer credit as prerequisite."),
    ]
    derived = {(p.oxford_code, p.atlanta_code) for p in _derive(courses, texts)}
    assert ("MATH_OX 112Z", "MATH 112Z") in derived
    assert ("MATH_OX 112Z", "MATH 112") not in derived


def test_the_course_number_alone_does_not_make_two_courses_equivalent():
    """PE_OX 101 and PE 101 share a number and are different courses.

    `PE_OX 101` is "Weight Training/Conditioning" and `PE 101` is "Personal
    Health". A pair the catalog never states is not a pair.
    """
    courses = [
        ("PE_OX 101", "Weight Training/Conditioning"),
        ("PE 101", "Personal Health"),
    ]
    assert _derive(courses, []) == ()


def test_two_records_with_the_same_number_and_title_are_the_same_course():
    """The records state the relation when no requirement names it."""
    courses = [
        ("MATH_OX 112", "Calculus II"),
        ("MATH 112", "Calculus II"),
        ("MATH 111", "Calculus I"),
    ]
    pairs = _derive(courses, [])
    assert ("MATH_OX 112", "MATH 112") in {
        (p.oxford_code, p.atlanta_code) for p in pairs}
    assert [p.basis for p in pairs
            if p.oxford_code == "MATH_OX 112"] == [RECORD_BASIS]


def test_a_course_with_no_atlanta_counterpart_has_no_equivalence():
    """An Oxford-only course is its own course."""
    courses = [
        ("OXST_OX 100R", "Oxford Studies"),
        ("MATH 112", "Calculus II"),
    ]
    assert _derive(courses, []) == ()


def test_the_campuses_writing_the_subject_differently_is_still_one_course():
    """Oxford writes ANTH where Atlanta writes ANT."""
    courses = [
        ("ANTH_OX 101", "Introduction to Anthropology"),
        ("ANT 101", "Introduction to Anthropology"),
    ]
    pairs = _derive(courses, [])
    assert ("ANTH_OX 101", "ANT 101") in {
        (p.oxford_code, p.atlanta_code) for p in pairs}


def test_titles_that_open_differently_are_not_the_same_course():
    """One campus's title can name the course more fully than the other's.

    "Introduction to Social and Political Philosophy" and "Political
    Philosophy" share words but open differently, and the catalog does not
    pair them, so they are not one course.
    """
    assert titles_compatible("Introduction to Social and Political Philosophy",
                             "Political Philosophy") is False
    assert titles_compatible("Foundations of Modern Biology I",
                             "Foundations of Modern Biol I") is True
    assert titles_compatible("Concepts in Biology", "Concepts In Biology") is True
    assert titles_compatible(None, "Calculus II") is None


def test_a_pair_is_always_between_the_two_campuses():
    """No course is its own counterpart, and the campuses do not pair with themselves."""
    pairs = _derive(
        [("MATH_OX 112", "Calculus II"), ("MATH 112", "Calculus II"),
         ("BIOL 142", "Foundations of Modern Biology II")],
        ["MATH 111 or MATH_OX 111 or equivalent transfer credit."],
    )
    for pair in pairs:
        assert is_oxford(pair.oxford_code)
        assert not is_oxford(pair.atlanta_code)
        assert pair.oxford_code != pair.atlanta_code


def test_the_derivation_is_deterministic():
    """The same corpus gives the same relation, so a re-import keeps it stable."""
    courses = [("MATH_OX 112", "Calculus II"), ("MATH 112", "Calculus II"),
               ("MATH 111", "Calculus I")]
    texts = ["MATH 111 or MATH_OX 111 or equivalent transfer credit."]
    assert _derive(courses, texts) == _derive(courses, texts)


def test_an_oxford_code_the_catalog_names_is_known_even_without_a_record():
    """The catalog's codes are the evidence; the master may lack the row.

    A requirement that offers "BIOL 142 or BIOL_OX 142" publishes both codes.
    """
    pairs = _derive(
        [("BIOL 142", "Foundations of Modern Biology II")],
        ["BIOL 142 or BIOL_OX 142 or BIOL 240 or equivalent transfer credit."],
    )
    derived = {(p.oxford_code, p.atlanta_code) for p in pairs}
    assert ("BIOL_OX 142", "BIOL 142") in derived


# --- what the database does with it ---------------------------------------

def test_the_import_records_the_equivalence(db, collection, tmp_path):
    """The relation is a row in the database, not a derived guess at read time."""
    import json

    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II",
                    "requisites": "MATH 111 or MATH_OX 111 or equivalent transfer credit."}),
        json.dumps({"code": "MATH 261", "title": "Biology of Insects",
                    "requisites": "MATH 112 or MATH_OX 112 or equivalent transfer credit."}),
        json.dumps({"code": "MATH 111", "title": "Calculus I"}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")
    rows = db.execute(
        "SELECT oxford_code, atlanta_code, basis FROM course_campus_equivalents "
        "WHERE collection_id = %(c)s ORDER BY oxford_code",
        {"c": collection},
    ).fetchall()
    assert ("MATH_OX 112", "MATH 112") in {
        (r["oxford_code"], r["atlanta_code"]) for r in rows}


def test_a_mention_naming_an_oxford_course_resolves_to_its_counterpart(
        db, collection, tmp_path):
    """The mention keeps its own code; the target it points at is the course.

    A requirement that says "MATH 112 or MATH_OX 112" names a course the
    master may not have a row for. The mention resolves to the Atlanta
    course it stands for.
    """
    import json

    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II"}),
        json.dumps({"code": "MATH 261", "title": "Biology of Insects",
                    "requisites": "MATH 112 or MATH_OX 112 or equivalent transfer credit."}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")
    row = db.execute(
        "SELECT m.target_code, c.code AS resolved "
        "FROM prereq_mentions m "
        "JOIN courses dependent ON dependent.course_id = m.course_id "
        "JOIN courses c ON c.course_id = m.target_course_id "
        "WHERE dependent.code = 'MATH 261' AND m.target_code = 'MATH_OX 112'",
        {},
    ).fetchone()
    assert row is not None
    assert row["target_code"] == "MATH_OX 112"
    assert row["resolved"] == "MATH 112"


def test_the_graph_traverses_either_campus_s_code(db, collection, tmp_path):
    """An edge that names an Oxford course reaches that course's counterpart.

    A requirement that says "MATH 112 or MATH_OX 112" names an Oxford course
    the master has no row for. Without the traversal the reader never sees
    what MATH 112 requires, and the answer says the details are unavailable.
    """
    import json

    from app.db import queries
    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II"}),
        json.dumps({"code": "MATH 261", "title": "Biology of Insects",
                    "requisites": "MATH 112 or MATH_OX 112 or equivalent transfer credit."}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")

    def course_id(code):
        return db.execute(
            "SELECT course_id FROM courses WHERE collection_id = %(c)s AND code = %(code)s",
            {"c": collection, "code": code}).fetchone()["course_id"]

    params = {"collection_id": collection, "source_ids": None, "workspace_key": None}
    upstream = db.execute(
        queries.get("prereq_upstream"),
        {**params, "course_id": course_id("MATH 261")}).fetchall()
    assert upstream, "the upstream passage of the counterpart must be reachable"

    downstream = db.execute(
        queries.get("prereq_downstream"),
        {**params, "course_id": course_id("MATH 112")}).fetchall()
    assert downstream, "the dependent must be reachable from the counterpart"


def test_the_loader_reads_the_table_in_both_directions(db, collection, tmp_path):
    """A student who took one campus's course has taken the other's."""
    import json

    from app.advising.catalog import load_campus_equivalents
    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II"}),
        json.dumps({"code": "MATH 261", "title": "Biology of Insects",
                    "requisites": "MATH 112 or MATH_OX 112 or equivalent transfer credit."}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")
    mapping = load_campus_equivalents(db, collection_id=collection)
    assert mapping["MATH 112"] == frozenset({"MATH_OX 112"})
    assert mapping["MATH_OX 112"] == frozenset({"MATH 112"})


def test_the_research_path_knows_a_course_the_catalog_publishes(
        db, collection, tmp_path):
    """An answer may name a course the master has no row for.

    The catalog publishes "MATH 112 or MATH_OX 112": the requirement names
    both codes, so both are courses. The master carries only the Atlanta one.
    An answer that names the Oxford code is naming a real course, and the
    identity check must not reject it as one that does not exist.
    """
    import json
    from types import SimpleNamespace

    from app.generation.research_tools import ResearchSession
    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II"}),
        json.dumps({"code": "MATH 261", "title": "Biology of Insects",
                    "requisites": "MATH 112 or MATH_OX 112 or equivalent transfer credit."}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")
    session = ResearchSession.__new__(ResearchSession)
    session.conn = db
    session.scope = SimpleNamespace(collection_id=collection)
    session._course_codes = None
    known = session._known_course_codes()
    assert "MATH_OX 112" in known
    assert "MATH 112" in known
    # A course nothing publishes is still not a course.
    assert "MATH 999" not in known


def test_unpaired_oxford_code_in_requisites_is_known(db, collection, tmp_path):
    """Catalog-named Oxford codes remain known even without an equivalence row."""
    import json
    from types import SimpleNamespace

    from app.generation.research_tools import ResearchSession
    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II",
                    "requisites": "MATH 112 or MATH_OX 117 or equivalent transfer credit."}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")
    rows = db.execute(
        "SELECT oxford_code, atlanta_code FROM course_campus_equivalents "
        "WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchall()
    assert ("MATH_OX 117", "MATH 112") not in {
        (r["oxford_code"], r["atlanta_code"]) for r in rows
    }
    session = ResearchSession.__new__(ResearchSession)
    session.conn = db
    session.scope = SimpleNamespace(collection_id=collection)
    session._course_codes = None
    known = session._known_course_codes()
    assert "MATH_OX 117" in known


def test_the_equivalence_is_not_the_retrieval_deduplication_key(
        db, collection, tmp_path):
    """The two campuses share a course, not a passage.

    `canonical_code` is the key retrieval deduplicates on. If the campuses
    shared it, one campus's passage would be deleted from the candidate set
    and the survivor would be relabelled, so the two must keep their own.
    """
    import json

    from tests.test_atlas_import import _import

    lines = [
        json.dumps({"code": "MATH 112", "title": "Calculus II"}),
        json.dumps({"code": "MATH 261", "title": "Biology of Insects",
                    "requisites": "MATH 112 or MATH_OX 112 or equivalent transfer credit."}),
    ]
    _import(db, collection, tmp_path, "\n".join(lines) + "\n")
    rows = db.execute(
        "SELECT code, canonical_code FROM courses WHERE collection_id = %(c)s "
        "AND code IN ('MATH 112', 'MATH_OX 112')",
        {"c": collection},
    ).fetchall()
    for row in rows:
        assert row["canonical_code"] == row["code"]


# --- what the planner does with it ----------------------------------------

def _requirement(code, prerequisites=None, corequisites=None, raw=None):
    return CourseRequirement(
        code=code, title=code, parse_status="parsed",
        prerequisites=prerequisites, corequisites=corequisites, requisites_raw=raw,
    )


def test_a_completed_campus_equivalent_satisfies_the_requirement():
    """A student who took MATH_OX 111 at Oxford has taken what MATH 111 asks for.

    The requirement names the Atlanta course; the student's record names the
    Oxford one. Without the equivalence the student is told they are missing
    a prerequisite they have already taken.
    """
    courses = {
        "CS 170": _requirement(
            "CS 170", prerequisites=AllOf(items=(CourseRef(code="MATH 111"),)),
            raw="MATH 111"),
    }
    blocked = plan_sequence(courses, ["CS 170"], ["MATH_OX 111"])
    assert [b.reason for b in blocked.blockers] == ["missing_prerequisite"]

    satisfied = plan_sequence(
        courses, ["CS 170"], ["MATH_OX 111"],
        {"MATH_OX 111": frozenset({"MATH 111"}),
         "MATH 111": frozenset({"MATH_OX 111"})},
    )
    assert satisfied.blockers == ()
    assert satisfied.stages == (("CS 170",),)


def test_the_student_plan_is_untouched_by_the_equivalence():
    """The equivalence changes what is satisfied, not what is planned.

    A student who completed the Oxford course and selected the Atlanta one
    still has the Atlanta one in their plan, because the plan reports the
    student's own courses.
    """
    courses = {
        "CS 170": _requirement(
            "CS 170", prerequisites=AllOf(items=(CourseRef(code="MATH 111"),)),
            raw="MATH 111"),
        "MATH 111": _requirement("MATH 111"),
    }
    plan = plan_sequence(
        courses, ["CS 170", "MATH 111"], ["MATH_OX 111"],
        {"MATH_OX 111": frozenset({"MATH 111"}),
         "MATH 111": frozenset({"MATH_OX 111"})},
    )
    # The student still takes MATH 111; the equivalence only removed the wait
    # for it, because the Oxford course already satisfies the requirement.
    assert any("MATH 111" in stage for stage in plan.stages)
    assert plan.blockers == ()


def test_without_the_equivalence_the_requirement_is_still_missing():
    """Without the relation the planner behaves exactly as before."""
    courses = {
        "CS 170": _requirement(
            "CS 170", prerequisites=AllOf(items=(CourseRef(code="MATH 111"),)),
            raw="MATH 111"),
    }
    plan = plan_sequence(courses, ["CS 170"], ["MATH_OX 111"])
    assert [b.reason for b in plan.blockers] == ["missing_prerequisite"]


def test_a_selected_campus_equivalent_satisfies_the_requirement():
    """The requirement names the other campus's offering of the student's course.

    The student is taking MATH_OX 111 at Oxford; the requirement names
    MATH 111. Without the relation the student is told they are missing a
    prerequisite they are taking.
    """
    equivalents = {"MATH_OX 111": frozenset({"MATH 111"}),
                   "MATH 111": frozenset({"MATH_OX 111"})}
    courses = {
        "CS 170": _requirement(
            "CS 170", prerequisites=AllOf(items=(CourseRef(code="MATH 111"),)),
            raw="MATH 111"),
        "MATH_OX 111": _requirement("MATH_OX 111"),
    }
    plan = plan_sequence(courses, ["CS 170", "MATH_OX 111"], [], equivalents)
    assert plan.blockers == ()
    # The student's own course is the one the plan reports.
    assert any("MATH_OX 111" in stage for stage in plan.stages)


def test_the_requirement_may_name_the_campus_equivalent_of_the_selection():
    """The requirement names the Oxford course; the student selected the Atlanta one."""
    equivalents = {"MATH_OX 111": frozenset({"MATH 111"}),
                   "MATH 111": frozenset({"MATH_OX 111"})}
    courses = {
        "CS 170": _requirement(
            "CS 170", prerequisites=AllOf(items=(CourseRef(code="MATH_OX 111"),)),
            raw="MATH_OX 111"),
        "MATH 111": _requirement("MATH 111"),
    }
    plan = plan_sequence(courses, ["CS 170", "MATH 111"], [], equivalents)
    assert plan.blockers == ()
    assert any("MATH 111" in stage for stage in plan.stages)


def test_two_campus_codes_of_one_course_are_not_two_alternatives():
    """The catalog offers the same course at two campuses.

    "MATH 111 or MATH_OX 111" is one choice, not two: the student who took
    either has taken the course. The planner must not read the two codes as
    two alternatives the student picked, and block the plan for it.
    """
    equivalents = {"MATH_OX 111": frozenset({"MATH 111"}),
                   "MATH 111": frozenset({"MATH_OX 111"})}
    courses = {
        "CS 170": _requirement(
            "CS 170",
            prerequisites=AnyOf(items=(CourseRef(code="MATH 111"),
                                       CourseRef(code="MATH_OX 111"))),
            raw="MATH 111 or MATH_OX 111"),
        "MATH 111": _requirement("MATH 111"),
        "MATH_OX 111": _requirement("MATH_OX 111"),
    }
    plan = plan_sequence(courses, ["CS 170", "MATH_OX 111"], [], equivalents)
    assert [b.reason for b in plan.blockers] == []


def test_taking_the_course_at_either_campus_is_one_choice():
    """The student took both codes; they are the same course, so it is not a conflict."""
    equivalents = {"MATH_OX 111": frozenset({"MATH 111"}),
                   "MATH 111": frozenset({"MATH_OX 111"})}
    courses = {
        "CS 170": _requirement(
            "CS 170",
            prerequisites=AnyOf(items=(CourseRef(code="MATH 111"),
                                       CourseRef(code="MATH_OX 111"))),
            raw="MATH 111 or MATH_OX 111"),
        "MATH 111": _requirement("MATH 111"),
        "MATH_OX 111": _requirement("MATH_OX 111"),
    }
    plan = plan_sequence(courses, ["CS 170", "MATH 111", "MATH_OX 111"], [], equivalents)
    assert [b.reason for b in plan.blockers] == []


def test_generic_titles_do_not_pair_across_departments() -> None:
    """Honors Research / Freshman Seminar numbers are shared; subjects are not.

    CHEM_OX 495A is CHEM 495A, never CS 495A or MATH 495A, even when the
    titles are identical.
    """
    courses = [
        ("CHEM_OX 495A", "Honors Research"),
        ("CHEM 495A", "Honors Research"),
        ("CS 495A", "Honors Research"),
        ("MATH 495A", "Honors Research"),
    ]
    derived = {(p.oxford_code, p.atlanta_code) for p in _derive(courses, [])}
    assert ("CHEM_OX 495A", "CHEM 495A") in derived
    assert ("CHEM_OX 495A", "CS 495A") not in derived
    assert ("CHEM_OX 495A", "MATH 495A") not in derived


def test_a_foreign_department_number_does_not_block_a_topic_variant() -> None:
    """ART 142Q existing in Atlanta must not stop BIOL_OX 142Q → BIOL 142."""
    courses = [
        ("BIOL 142", "Foundations of Modern Biology II"),
        ("ART 142Q", "Art Topics"),
    ]
    texts = [
        "BIOL 142 or BIOL_OX 142Q or equivalent transfer credit as prerequisite.",
    ]
    derived = {(p.oxford_code, p.atlanta_code) for p in _derive(courses, texts)}
    assert ("BIOL_OX 142Q", "BIOL 142") in derived
