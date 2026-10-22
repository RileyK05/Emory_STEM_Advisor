"""Program requirement parsing and import. Deterministic; no models."""

import json
import uuid

import pytest

from app.db import queries
from app.programs.importer import import_programs, program_id
from app.programs.requirements import choose_count, course_mentions, parse_program, to_record
from tests.test_atlas_import import _import as import_atlas_sample

# Shapes copied from the 2026-2027 catalog PDF.
CS_BS = """Computer Science (BS)
DEGREE AWARDED BS
HOURS TO COMPLETE 56
COURSES REQUIRED 18
DEPARTMENT CONTACT Computer Science
Requirements
Computer Science BS Requirements:
• Math 111
• CS 170
• One from the following: CS 370, CS 371W
• Three additional 300-level CS courses (one of the three courses may be chosen from Math 315,
Math 346, or Math 362)
Electives (select 2 courses required, 6 CU)
• Econ 312: Economics of Digital Assets (3 CU)
"""


@pytest.mark.parametrize(("text", "codes"), [
    ("Math 211, 250, 318, 321, 421-422", ["MATH 211", "MATH 250", "MATH 318", "MATH 321",
                                          "MATH 421", "MATH 422"]),
    ("Physics 151-152", ["PHYS 151", "PHYS 152"]),
    ("Biology 141 and 141L plus Biology 142 and 142L", ["BIOL 141", "BIOL 141L", "BIOL 142",
                                                        "BIOL 142L"]),
    ("Biol/Psych 320, Biol 356/NBB 358", ["BIOL 320", "PSYC 320", "BIOL 356", "NBB 358"]),
    ("ENVS 230 [or ENVS OX 230*]", ["ENVS 230", "ENVS_OX 230"]),
    ("ECON 480/CS 480: Computing Methods", ["ECON 480", "CS 480"]),
    ("DATASCI 220 or QTM 220", ["DATASCI 220", "QTM 220"]),
    # Not codes: levels, counts, and capitalized words that aren't subjects.
    ("Any 300-level CS classroom course", []),
    ("Enrollment in ALL 300-level and above", []),
    ("at least 18 credit hours, 3 courses at the 200 level", []),
    # A non-subject word before a number must not carry the previous subject.
    ("Math 211, 250, and Classics 350", ["MATH 211", "MATH 250"]),
    ("Two QTM 100-level courses", []),
    ("MATH 200-300 level", []),
])
def test_course_mentions(text, codes):
    assert list(course_mentions(text)) == codes


def test_choose_count_only_when_explicit():
    assert choose_count("One from the following: CS 370, CS 371W") == 1
    assert choose_count("Electives (select 2 courses required, 6 CU)") == 2
    assert choose_count("Two courses chosen from: CS 442, CS 443") == 2
    assert choose_count("Three additional 300-level CS courses") is None
    # A credit value must not be read as the count.
    assert choose_count("MATH 351 (3 credits) or one of the following") == 1
    assert choose_count("MATH 111 (4 credits) Two courses from the following") == 2


def test_parse_program_metadata_sections_and_wrapped_lines():
    program = parse_program("Computer Science (BS)", CS_BS)
    assert (program.kind, program.degree, program.hours, program.courses_required,
            program.department) == ("major", "BS", "56", "18", "Computer Science")
    by_text = {item.text: item for item in program.items}
    wrapped = next(i for i in program.items if i.text.startswith("Three additional"))
    assert wrapped.courses == ("MATH 315", "MATH 346", "MATH 362")  # joined across the wrap
    choice = by_text["One from the following: CS 370, CS 371W"]
    assert (choice.section, choice.choose) == ("Computer Science BS Requirements", 1)
    assert by_text["Econ 312: Economics of Digital Assets (3 CU)"].section == (
        "Electives (select 2 courses required, 6 CU)")
    assert program.courses[:3] == ("MATH 111", "CS 170", "CS 370")


def test_parse_program_tolerates_catalog_metadata_typos():
    """EMO-019: "DEZGREE AWRDED" / "HOURS TO COMPLET" must still parse."""
    text = ("CHEMISTRY (BA)\nDEZGREE AWRDED BA\nHOURS TO COMPLET 56\n"
            "COURSES REQUIRED 18\nDEPARTMENT CONTACTS Chemistry\n"
            "Requirements\nCHEM 150\nCHEM 202\n")
    program = parse_program("CHEMISTRY (BA)", text)
    assert (program.kind, program.degree, program.hours, program.courses_required,
            program.department) == ("major", "BA", "56", "18", "Chemistry")


def _write_programs(tmp_path, *titles_and_texts):
    path = tmp_path / "programs.jsonl"
    lines = [json.dumps(to_record(parse_program(title, text), catalog="Test Catalog",
                                  source_url="https://example.test/catalog.pdf",
                                  first_page=1, last_page=1))
             for title, text in titles_and_texts]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


ECON_MINOR = """Economics (Minor)
DEGREE AWARDED N/A
Requirements
• Econ 101 and Econ 201
• Math 111 or Math 112
"""


def test_import_links_mentions_and_relinks_after_atlas_reimport(db, collection, tmp_path):
    import_atlas_sample(db, collection, tmp_path)
    path = _write_programs(tmp_path, ("Economics (Minor)", ECON_MINOR))
    counts = import_programs(db, collection_id=collection, path=path)
    # A record without a manifest source identity stays unbound: nothing is guessed.
    assert counts == {"programs": 1, "mentions": 4, "resolved": 4, "bound": 0,
                      "evidence_unresolved": 0, "unbound": 1}

    pid = program_id(collection, "Economics (Minor)")
    items = db.execute(queries.get("list_program_items"), {"program_id": pid}).fetchall()
    assert [list(i["courses"]) for i in items] == [[], ["ECON 101", "ECON 201"],
                                                   ["MATH 111", "MATH 112"]]
    requiring = db.execute(queries.get("programs_requiring_course"),
                           {"collection_id": collection, "code": "MATH 112"}).fetchall()
    assert [r["title"] for r in requiring] == ["Economics (Minor)"]

    # Re-importing the atlas replaces the course rows; program links follow.
    import_atlas_sample(db, collection, tmp_path)
    again = db.execute(queries.get("program_mention_counts"),
                       {"collection_id": collection}).fetchone()
    assert (again["mentions"], again["resolved"]) == (4, 4)

    # Re-importing programs replaces them rather than duplicating.
    assert import_programs(db, collection_id=collection, path=path)["programs"] == 1
    total = db.execute(queries.get("list_programs"), {"collection_id": collection}).fetchall()
    assert len(total) == 1


def test_plural_department_contacts_span_lines():
    text = ("ECONOMICS AND HUMAN HEALTH (BA)\nDEGREE AWARDED BA\nHOURS TO COMPLETE 45\n"
            "DEPARTMENT CONTACTS Human Health\nEconomics\nRequirements\n• Econ 101\n")
    program = parse_program("ECONOMICS AND HUMAN HEALTH (BA)", text)
    assert program.department == "Human Health; Economics"
    assert program.items[0].text == "Requirements"


def test_duplicate_catalog_programs_keep_first_and_conflicts_fail():
    from app.programs.catalog_pdf import CatalogSection
    from scripts.scrape_programs import dedupe_programs

    body = "Requirements\n• Arabic 101, 102\n"
    first = CatalogSection("Arabic (Minor)", "Arabic (Minor)\nDEPARTMENT CONTACT MESAS\n" + body,
                           45, 45)
    again = CatalogSection("Arabic (Minor)", "Arabic (Minor)\nDEPARTMENT CONTACTS MESAS\n" + body,
                           93, 94)
    assert dedupe_programs([first, again]) == [first]
    changed = CatalogSection("Arabic (Minor)", "Arabic (Minor)\nRequirements\n• Arabic 201\n",
                             93, 94)
    with pytest.raises(ValueError, match="two different programs"):
        dedupe_programs([first, changed])


def test_reimport_keeps_saved_release_and_audit(db, collection, tmp_path):
    """An identical program reimport leaves the published release and saved audit in place."""
    from tests.helpers import api_client

    title = "Pocket Major (BA)"
    text = "Pocket Major (BA)\nDEGREE AWARDED BA\nRequirements\n• CS 170\n"
    identity = _ingest_catalog_file(db, collection, tmp_path, title, text, name="pocket.md")
    record = to_record(parse_program(title, text), catalog="Test Catalog",
                       source_url="https://example.test/catalog.pdf", first_page=3, last_page=4)
    record["source"] = identity
    path = tmp_path / "pocket.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert import_programs(db, collection_id=collection, path=path)["bound"] == 1
    pid = program_id(collection, title)
    client = api_client(db, collection)
    draft = client.post(f"/api/admin/programs/{pid}/rules/draft", json={"notes": "reviewed"})
    assert draft.status_code == 201, draft.text
    release_id = draft.json()["releaseId"]
    assert client.post(f"/api/admin/programs/{pid}/rules/{release_id}/approve", json={}).status_code == 200
    published = client.post(f"/api/admin/programs/{pid}/rules/{release_id}/publish", json={})
    assert published.status_code == 200, published.text
    record_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    db.execute(
        "INSERT INTO student_academic_records (record_id, workspace_key) VALUES (%s, 'development')",
        (record_id,),
    )
    db.execute(
        """
        INSERT INTO degree_audits (
            audit_id, workspace_key, program_id, rule_release_id, record_id, record_revision,
            rules_digest, overall_status
        ) VALUES (%s, 'development', %s, %s, %s, 1, %s, 'outstanding')
        """,
        (audit_id, pid, release_id, record_id, published.json()["rulesDigest"]),
    )
    db.commit()
    reviews_before = db.execute(
        "SELECT count(*) AS n FROM program_rule_reviews WHERE release_id = %s", (release_id,),
    ).fetchone()["n"]

    assert import_programs(db, collection_id=collection, path=path)["bound"] == 1

    release = db.execute(
        "SELECT status FROM program_rule_releases WHERE release_id = %s", (release_id,),
    ).fetchone()
    audit = db.execute(
        "SELECT overall_status FROM degree_audits WHERE audit_id = %s", (audit_id,),
    ).fetchone()
    reviews_after = db.execute(
        "SELECT count(*) AS n FROM program_rule_reviews WHERE release_id = %s", (release_id,),
    ).fetchone()["n"]
    assert release["status"] == "published"
    assert audit["overall_status"] == "outstanding"
    assert reviews_after == reviews_before


def test_import_commits_even_inside_an_open_transaction(db, collection, tmp_path, migrated_dsn):
    from app.db.connection import connect

    path = _write_programs(tmp_path, ("Economics (Minor)", ECON_MINOR))
    db.execute("SELECT 1")  # leaves an implicit transaction open, as a CLI lookup does
    import_programs(db, collection_id=collection, path=path)
    other = connect(migrated_dsn)
    try:
        rows = other.execute(queries.get("list_programs"),
                             {"collection_id": collection}).fetchall()
    finally:
        other.close()
    assert [r["title"] for r in rows] == ["Economics (Minor)"]


def test_nbb_shapes_short_lines_stay_separate_and_suffixes_parse():
    text = ("NEUROSCIENCE AND BEHAVIOUR BIOLOGY (BS)\nDEGREE AWARDED BS\nRequirements\n"
            "NBB 301 (Bio 360) Introduction to Neurobiology\n"
            "NBB 401/402W Perspectives in Neuroscience and Behavior\n"
            "Elective Course\n"
            "*Chem 202z/202zL,\n")
    program = parse_program("NEUROSCIENCE AND BEHAVIOUR BIOLOGY (BS)", text)
    assert [(i.text, i.courses) for i in program.items[1:]] == [
        ("NBB 301 (Bio 360) Introduction to Neurobiology", ("NBB 301", "BIOL 360")),
        ("NBB 401/402W Perspectives in Neuroscience and Behavior", ("NBB 401", "NBB 402W")),
        ("Elective Course", ()),
        ("*Chem 202z/202zL,", ("CHEM 202Z", "CHEM 202ZL")),
    ]


def _ingest_catalog_file(db, collection, tmp_path, title, text, *, name=None):
    """Write and ingest a catalog program file the way the scraper and ingest CLI do."""
    import hashlib

    from app.ingest.pipeline import ingest_file
    from app.programs.catalog_pdf import CatalogSection
    from scripts.scrape_programs import catalog_origin_key, render_section
    from tests.fakes import FakeEmbedder, whitespace_tokens
    from tests.helpers import MODEL, lenient_config

    path = tmp_path / (name or "program.md")
    path.write_text(render_section(CatalogSection(title, text, 3, 4), catalog="Test Catalog",
                                   catalog_id="CAT1"), encoding="utf-8", newline="\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    ingest_file(db, collection_id=collection, path=path, content_type="text/markdown",
                count_tokens=whitespace_tokens, embedder=FakeEmbedder(8), embed_model=MODEL,
                config=lenient_config(), origin_key=catalog_origin_key(title))
    db.commit()
    return {"origin_key": catalog_origin_key(title), "content_sha256": sha, "catalog_id": "CAT1"}


@pytest.mark.parametrize("case, status", [
    ("exact identity", "bound"),
    ("wrong content hash", "unbound"),
    ("superseded version", "unbound"),
    ("line absent from source", "evidence_unresolved"),
])
def test_program_binds_only_to_its_exact_current_source(db, collection, tmp_path, case, status):
    identity = _ingest_catalog_file(db, collection, tmp_path, "Computer Science (BS)", CS_BS)
    text = CS_BS
    if case == "wrong content hash":
        identity = {**identity, "content_sha256": "0" * 64}
    if case == "superseded version":
        # A newer catalog file for the same program replaces the bound version.
        _ingest_catalog_file(db, collection, tmp_path, "Computer Science (BS)",
                             CS_BS + "• CS 999\n", name="program-v2.md")
    if case == "line absent from source":
        text = CS_BS.replace("• CS 170", "• CS 171")
    record = to_record(parse_program("Computer Science (BS)", text), catalog="Test Catalog",
                       source_url="https://example.test/catalog.pdf", first_page=3, last_page=4)
    record["source"] = identity
    path = tmp_path / "programs.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    counts = import_programs(db, collection_id=collection, path=path)
    assert counts[status] == 1
    pid = program_id(collection, "Computer Science (BS)")
    binding = db.execute(queries.get("get_program_binding"), {"program_id": pid}).fetchone()
    assert binding["binding_status"] == status
    unsupported = db.execute(queries.get("list_program_items_without_evidence"),
                             {"program_id": pid}).fetchall()
    if status == "unbound":
        assert binding["source_id"] is None
        assert len(unsupported) == len(record["items"])  # every line stays a lead
        return
    # Every supported line's evidence is the exact text in its chunk, by digest.
    import hashlib
    evidence = db.execute(queries.get("list_program_item_evidence"),
                          {"program_id": pid}).fetchall()
    for row in evidence:
        chunk = db.execute("SELECT text FROM chunks WHERE chunk_id = %(c)s",
                           {"c": row["chunk_id"]}).fetchone()["text"]
        exact = chunk[row["span_start"]:row["span_end"]]
        assert hashlib.sha256(exact.encode()).hexdigest() == row["text_digest"]
    fields = {row["field"] for row in db.execute(
        queries.get("list_program_field_evidence"), {"program_id": pid}).fetchall()}
    assert fields == {"title", "degree", "hours", "courses_required", "department"}
    if status == "bound":
        assert unsupported == []
    else:
        assert [row["text"] for row in unsupported] == ["CS 171"]


@pytest.mark.parametrize("change", ["same", "changed", "removed", "unbound"])
def test_program_refresh_retains_released_rules_and_saved_audits(db, collection, tmp_path, change):
    from app.advising.audit_service import DegreeAuditService
    from app.auth.models import Principal
    from app.programs.review_service import ProgramReviewService

    title = "Computer Science (BS)"
    identity = _ingest_catalog_file(db, collection, tmp_path, title, CS_BS)
    record = to_record(parse_program(title, CS_BS), catalog="Test Catalog",
                       source_url="https://example.test/catalog.pdf", first_page=3, last_page=4)
    record["source"] = identity
    path = tmp_path / "programs.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    import_programs(db, collection_id=collection, path=path)
    pid = program_id(collection, title)
    release = ProgramReviewService.create_rule_draft(db, pid, None)
    ProgramReviewService.approve_rule_release(db, release["release_id"], None)
    ProgramReviewService.publish_rule_release(db, release["release_id"], None)
    student = Principal("development", None, "development")
    saved = DegreeAuditService.run_audit(db, student.workspace_key, pid, student)
    db.commit()

    if change == "changed":
        record["items"][1]["text"] += " (review updated wording)"
    elif change == "unbound":
        record["source"] = {**identity, "content_sha256": "0" * 64}
    if change == "removed":
        # A genuinely removed program is a non-empty import that no longer
        # contains its title. An empty file is rejected before any retirement.
        other_title = "Other Program (BA)"
        other_text = CS_BS.replace("Computer Science (BS)", other_title)
        other_identity = _ingest_catalog_file(
            db, collection, tmp_path, other_title, other_text, name="other.md"
        )
        other = to_record(
            parse_program(other_title, other_text), catalog="Test Catalog",
            source_url="https://example.test/catalog.pdf", first_page=3, last_page=4,
        )
        other["source"] = other_identity
        path.write_text(json.dumps(other) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    import_programs(db, collection_id=collection, path=path)

    retained = db.execute(queries.get("get_program_rule_release"), {
        "release_id": release["release_id"],
    }).fetchone()
    assert retained["rules_tree"] == release["rules_tree"]
    assert retained["status"] == ("published" if change == "same" else "archived")
    audit = DegreeAuditService.get_audit(
        db, student.workspace_key, saved["audit"]["audit_id"], student,
    )["audit"]
    assert audit["is_stale"] == (change != "same")
    assert audit["rules_digest"] == release["rules_digest"]
    listed = db.execute(queries.get("list_programs"), {"collection_id": collection}).fetchall()
    active_ids = {row["program_id"] for row in listed}
    if change == "removed":
        assert pid not in active_ids
        assert [row["title"] for row in listed] == ["Other Program (BA)"]
    else:
        assert active_ids == {pid}


def test_alignment_steps_over_separator_gaps_but_never_long_holes():
    from uuid import uuid4

    from app.programs.binding import SourceChunk, SourceText

    def text(chunks):
        return SourceText([SourceChunk(uuid4(), None, start, start + len(body), body)
                           for start, body in chunks])

    # The chunker drops the newline between two locator regions from both chunks.
    joined = text([(0, "Choose any 4 courses"), (21, "at the 300 level.")])
    found = joined.find("Choose any 4 courses at the 300 level.")
    assert found == (0, 38) and len(joined.support(*found)) == 2
    holed = text([(0, "Choose any 4 courses"), (30, "at the 300 level.")])
    found = holed.find("Choose any 4 courses at the 300 level.")
    assert found is None or holed.support(*found) == ()
