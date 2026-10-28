"""Offline regression for requisite strings collected from the live 2026–27 catalog."""

import json
from collections import Counter
from pathlib import Path

import pytest

from app.atlas.authority import prepare_authoritative_courses
from app.atlas.importer import unresolved_courses
from app.atlas.offerings import OfferingRecord, ValidatedOfferings
from app.atlas.records import AtlasCourse, canonical_code_map, normalize_code
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
    assert parse_requisites(by_code["CS 350"]["requisites"]).trees["prerequisite"] == AnyOf((
        AllOf((CourseRef("CS 253"), CourseRef("CS 255"))),
        Condition("equivalent transfer credit"),
    ))
    # A real unparenthesized mix (no trailing clause to blame) stays ambiguous.
    assert parse_requisites(by_code["MATH 345"]["requisites"]).status == "ambiguous"
    # The run-together conjunction is repaired, but the and/or mix remains.
    assert parse_requisites(by_code["BIOL 142"]["requisites"]).status == "ambiguous"
    # The explicit cannot-take exclusion remains in the source text but must
    # not invent BIOL 301 as a prerequisite or suppress the legitimate rule.
    chemistry = parse_requisites(by_code["CHEM 340"]["requisites"])
    assert chemistry.status == "parsed"
    assert {mention.code for mention in chemistry.mentions} == {
        "CHEM 204", "CHEM_OX 204", "CHEM 204L", "CHEM_OX 204L", "BIOL 141", "BIOL_OX 141",
    }


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


def test_live_offerings_override_catalog_and_preserve_exact_provenance_without_aliases():
    row = OfferingRecord(
        result_cursor=0, raw_course_code="MATH 221", title="Atlas Calculus",
        class_number="12345", group_identity="g1", is_grouped=False,
        matched_identity="MATH 221", srcdb="db", credits="4", description="Atlas desc",
        restrictions="MATH 111", notes="Permission required", notices=(("consent", "Instructor"),),
        raw_metadata={"sections": [{"instructor": "A", "meeting": "MWF"}]},
        evidence_hash="evidence-1", review_status="eligible", observed_at="2026-01-01T00:00:00Z",
        capture_envelope_hash="envelope-1",
    )
    snapshot = ValidatedOfferings(
        term="2026FA", career="Undergraduate", announced_count=1, search_criteria="all",
        index_evidence_hash="index", index_observed_at="2026-01-01T00:00:00Z",
        index_capture_envelope_hash="index-envelope", records=(row,), capture_envelopes=(),
    )
    base = [AtlasCourse(code="MATH 221", title="Catalog title", crosslisted=["MATH_OX 221"]),
            AtlasCourse(code="MATH_OX 221", title="Oxford catalog")]
    merged = prepare_authoritative_courses(base, (snapshot,), term="2026FA")
    by_code = {course.code: course for course in merged}
    assert by_code["MATH 221"].title == "Atlas Calculus"
    assert by_code["MATH 221"].authority == "atlas"
    assert by_code["MATH 221"].crosslisted == []
    assert by_code["MATH_OX 221"].authority == "catalog"
    assert by_code["MATH 221"].evidence[0]["capture_set_hash"] == snapshot.capture_set_hash
    assert by_code["MATH 221"].evidence[0]["notices"]["consent"] == "Instructor"
    assert by_code["MATH 221"].registration_notes == [
        "2026FA; Undergraduate; class 12345: Consent required: Instructor",
        "2026FA; Undergraduate; class 12345: Permission required",
    ]
    assert by_code["MATH 221"].requisites == "MATH 111"

    grouped_row = row.__class__(**{**row.__dict__, "class_number": None, "is_grouped": True,
                                    "notes": "Varies by section", "notices": ()})
    grouped_snapshot = snapshot.__class__(**{**snapshot.__dict__, "records": (grouped_row,)})
    grouped = prepare_authoritative_courses([], [grouped_snapshot], term="2026FA")[0]
    assert grouped.registration_notes == ["2026FA; Undergraduate; grouped result: Varies by section"]
    assert grouped.requisites == "MATH 111"

    # A catalog cross-list edge cannot back-link an overridden Atlas identity.
    rel = row.__class__(**{**row.__dict__, "raw_course_code": "REL 325",
                           "matched_identity": "REL 325", "title": "Atlas Religion"})
    scoped = snapshot.__class__(**{**snapshot.__dict__, "records": (row, rel)})
    catalog_link = AtlasCourse(code="AAS 325", title="Catalog", crosslisted=["REL 325"])
    canonical = canonical_code_map(prepare_authoritative_courses(
        [catalog_link], [scoped], term="2026FA"
    ))
    assert canonical["AAS 325"] != canonical["REL 325"]


@pytest.mark.parametrize(("titles", "title", "topics", "catalog"), [
    (["Structure and Properties"] * 2, "Structure and Properties", None, None),
    # One topic this term: the catalog course title marks where the topic starts.
    (["Special Topics: Printmaking: Painterly Prints"], "Special Topics",
     "Printmaking: Painterly Prints", "Special Topics"),
    (["Special Studies in the History of Art: Modeling Space + Form"],
     "Special Studies in the History of Art: Modeling Space + Form", None,
     "Special Studies History of Art"),
    # One topic offering's title names that topic, not the course.
    (["Special Topics: AAS: Wade in the Water", "Special Topics: AAS: Made in Her Image"],
     "Special Topics: AAS", "Made in Her Image; Wade in the Water", None),
    # Topics that share leading words still cut back to the ": " boundary.
    (["Topics: Black Nihilism", "Topics: Black Culture"],
     "Topics", "Black Culture; Black Nihilism", None),
    # A bare base title listed beside its topics is the course title.
    (["Topics in Religion", "Topics in Religion: Justice and the Commons"],
     "Topics in Religion", "Justice and the Commons", None),
    (["Calculus I", "Linear Algebra"], None, None, None),
])
def test_topic_offerings_share_the_course_title_and_keep_topics_as_notes(
    titles, title, topics, catalog
):
    rows = tuple(OfferingRecord(
        result_cursor=index, raw_course_code="AAS 285", title=value,
        class_number=str(index), group_identity="group", is_grouped=True,
        matched_identity="AAS 285", srcdb="db", credits="4", description="Topics vary.",
        restrictions=None, notes=None, notices=(), raw_metadata={"sections": []},
        evidence_hash=f"e{index}", review_status="eligible",
        observed_at="2026-01-01T00:00:00Z", capture_envelope_hash=f"c{index}",
    ) for index, value in enumerate(titles))
    snapshot = ValidatedOfferings(
        term="2026FA", career="Undergraduate", announced_count=len(rows), search_criteria="all",
        index_evidence_hash="index", index_observed_at="2026-01-01T00:00:00Z",
        index_capture_envelope_hash="index-envelope", records=rows, capture_envelopes=(),
    )
    base = [AtlasCourse(code="AAS 285", title=catalog)] if catalog else []
    if title is None:
        with pytest.raises(ValueError, match="no course title"):
            prepare_authoritative_courses(base, [snapshot], term="2026FA")
        return
    course = prepare_authoritative_courses(base, [snapshot], term="2026FA")[0]
    assert course.title == title
    assert "; ".join(topic["topic"] for topic in course.topics) == (topics or "")
    assert all(topic["class_numbers"] for topic in course.topics)
    assert course.registration_notes == []


def test_offering_restriction_variants_fail_closed_and_require_one_exact_term():
    def offering(class_number, restriction, evidence):
        return OfferingRecord(
            result_cursor=class_number, raw_course_code="CS 253", title="Live title",
            class_number=str(class_number), group_identity="group", is_grouped=True,
            matched_identity="CS 253", srcdb="db", credits="3", description="description",
            restrictions=restriction, notes=None, notices=(), raw_metadata={"sections": []},
            evidence_hash=evidence, review_status="eligible", observed_at="2026-01-01T00:00:00Z",
            capture_envelope_hash=f"capture-{evidence}",
        )
    snapshot = ValidatedOfferings(
        term="2026FA", career="Undergraduate", announced_count=2, search_criteria="all",
        index_evidence_hash="index", index_observed_at="2026-01-01T00:00:00Z",
        index_capture_envelope_hash="index-envelope",
        records=(offering(1, "MATH 111", "a"), offering(2, "MATH 112", "b")),
        capture_envelopes=(),
    )
    course = prepare_authoritative_courses([], [snapshot], term="2026FA")[0]
    assert "unresolved" in course.requisites
    assert len(course.evidence) == 2
    assert parse_requisites(course.requisites).status != "parsed"
    with pytest.raises(ValueError, match="exact term"):
        prepare_authoritative_courses([], [snapshot], term="2027SP")
