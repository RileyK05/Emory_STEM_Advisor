"""Catalog and program-page scrapers. Mock transports only; no network."""

import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.atlas.catalog import (
    course_from_page,
    course_record,
    credits_text,
    decode_nuxt_payload,
    sitemap_paths,
    write_jsonl,
)
from app.atlas.live import DetailRecord, IndexRecord, read_capture
from app.atlas.records import load_atlas
from app.atlas.requisites import AnyOf, Condition, CourseRef, parse_requisites
from app.ingest.advising_documents import prepare_documents
from app.programs.pages import page_to_markdown
from scripts import scrape_catalog, scrape_programs
from tests.fixtures.pdf import make_pdf

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


def test_live_detail_capture_preserves_raw_identity_boundaries_and_restrictions():
    capture = {
        "kind": "detail", "source_url": "https://atlas.emory.edu/", "term": "Spring 2027",
        "career": "Emory College", "observed_at": "2026-10-20T00:00:00Z",
        "html": '''<div class="dtl-course-code">CS 171</div>
          <div class="dtl-section">Section 1, Class Nbr 2961</div>
          <div class="detail-title">Introduction to Computer Science II</div>
          <div class="detail-hours_html"><b>Credit Hours:</b> 3</div>
          <div class="section section--description"><div class="section__content">Description.</div></div>
          <div class="section section--registration_restrictions"><div class="section__content"><p>This course requires CS 170 OR CS_OX 170  or equivalent transfer credit as a prerequisite.</p><p>Permission of instructor is also required.</p></div></div>
          <a class="course-section" data-key="crn:2961" data-group="code:CS 171" data-matched="crn:2961,3017" data-srcdb="5271"><div class="course-section-camp">Campus: ATL@ATLANTA</div></a>
          <a class="course-section" data-key="crn:3017" data-group="code:CS 171" data-matched="crn:2961,3017" data-srcdb="5271"></a>''',
    }
    record = read_capture(capture)
    assert isinstance(record, DetailRecord)
    assert (record.code, record.title, record.credits, record.class_number) == (
        "CS 171", "Introduction to Computer Science II", "3", "2961"
    )
    assert record.restrictions == (
        "This course requires CS 170 OR CS_OX 170 or equivalent transfer credit as a prerequisite. "
        "Permission of instructor is also required."
    )
    assert [section.crn for section in record.sections] == ["2961", "3017"]
    assert record.sections[0].campus == "ATL@ATLANTA"
    assert len(record.evidence_hash) == 64


def test_live_detail_capture_keeps_exact_unusual_code_and_missing_restrictions_distinct():
    capture = {
        "kind": "detail", "source_url": "https://atlas.emory.edu/", "term": "Spring 2027",
        "career": "Emory College", "observed_at": "2026-10-20T00:00:00Z", "extra": {},
        "html": """<div class="dtl-course-code">RES999PUBH</div>
          <div class="dtl-section">Class Nbr 77</div><div class="detail-title">A &amp; B \u2014 \u03a9</div>
          <div class="detail-hours_html">Credit Hours: 1</div>
          <div class="section section--description"><div class="section__content">First sentence.\nFull exclusions &amp; details.</div></div>
          <div class="section section--classnotes"><div class="section__content">All nursing pre-reqs require department permission.</div></div>
          <div class="detail-consent">Department approval required.</div>
          <a class="course-section" data-key="crn:77" data-group="code:RES999PUBH"
             data-matched="crn:77" data-srcdb="5271"><div class="course-section-camp">Campus: ATL</div></a>"""
    }
    record = read_capture(capture)
    assert isinstance(record, DetailRecord)
    assert record.code == "RES999PUBH"
    assert record.title == "A & B \u2014 \u03a9"
    assert record.description == "First sentence. Full exclusions & details."
    assert record.restrictions is None
    assert record.sections[0].crn == "77"
    assert record.notes == "All nursing pre-reqs require department permission."
    assert ("consent", "Department approval required.") in record.notices


def test_live_group_detail_without_class_number_preserves_all_sections_and_flags_review():
    capture = {
        "kind": "detail", "source_url": "https://atlas.emory.edu/", "term": "Spring 2027",
        "career": "Graduate Nursing", "observed_at": "2026-10-20T00:00:00Z",
        "html": '''<div class="dtl-course-code">NRSG201</div>
          <div class="detail-title">Nursing Research</div>
          <div class="detail-inst_method_code">Instruction Method: Varies by section</div>
          <div class="section section--classnotes"><div class="section__content">Prerequisites vary by section; department permission may be required.</div></div>
          <a class="course-section" data-key="crn:3004" data-group="code:NRSG201"
             data-matched="crn:3004,2385" data-srcdb="5271"><div class="course-section-section">Section #: 1</div></a>
          <a class="course-section" data-key="crn:2385" data-group="code:NRSG201"
             data-matched="crn:3004,2385" data-srcdb="5271"><div class="course-section-section">Section #: 2</div></a>''',
    }
    record = read_capture(capture)
    assert isinstance(record, DetailRecord)
    assert record.class_number is None and record.is_grouped
    assert record.section_variation_review
    assert [section.crn for section in record.sections] == ["3004", "2385"]
    assert record.notes == "Prerequisites vary by section; department permission may be required."


def _index_capture(rows: str, count: int = 2):
    return {
        "kind": "index", "source_url": "https://atlas.emory.edu/", "term": "Spring 2027",
        "career": "Emory College", "observed_at": "2026-10-20T00:00:00Z", "extra": None,
        "html": f'''<div class="panel__info-bar-text">Found <strong>{count}</strong> courses</div>
          <div class="result-criteria"><strong>Search Criteria</strong>
          <ul class="result-criteria__list"><li class="result-criteria__item">Term: Spring 2027</li>
          <li class="result-criteria__item">Career: Emory College</li></ul></div>{rows}''',
    }


def test_live_index_capture_keeps_multiple_variant_groups():
    rows = '''<a class="result__link" data-group="code:M2M 101" data-key=""
      data-matched="crn:1,2" data-srcdb="5271"><span class="result__code">M2M 101</span>
      <span class="result__title">Topics in African American Studies: Black Nihilism &amp; One</span></a>
      <a class="result__link" data-group="code:M2M_OX 101" data-key="crn:2"
      data-matched="crn:2" data-srcdb="5271"><span class="result__code">M2M_OX 101</span>
      <span class="result__title">Title Two</span></a>'''
    record = read_capture(_index_capture(rows))
    assert isinstance(record, IndexRecord)
    assert [row.group for row in record.rows] == ["code:M2M 101", "code:M2M_OX 101"]
    assert record.rows[0].title == "Topics in African American Studies: Black Nihilism & One"


def test_live_index_capture_rejects_duplicate_class_crns():
    row = '''<a class="result__link" data-group="code:MATH 111" data-key="crn:1"
      data-matched="crn:1" data-srcdb="5271"><span class="result__code">MATH 111</span>
      <span class="result__title">Calculus</span></a>'''
    with pytest.raises(ValueError, match="duplicate class CRNs"):
        read_capture(_index_capture(row + row))


def test_live_index_capture_rejects_criteria_mismatch():
    capture = _index_capture("")
    capture["html"] = capture["html"].replace("Spring 2027", "Fall 2027")
    with pytest.raises(ValueError, match="term disagrees"):
        read_capture(capture)


def _nuxt_page(payload_table: list) -> str:
    return (
        "<html><body><script type=\"application/json\" data-nuxt-data=\"nuxt-app\" "
        f"id=\"__NUXT_DATA__\">{json.dumps(payload_table)}</script></body></html>"
    )


# A devalue table: values refer to each other by index, wrapped in ShallowReactive.
CS370_PAGE = _nuxt_page([
    ["ShallowReactive", 1],
    {"data": 2},
    ["ShallowReactive", 3],
    {"course-0047402-undefined": 4},
    {"course": 5, "terms": 15},
    {"code": 6, "subjectCode": 7, "courseNumber": 8, "longName": 9, "description": 10,
     "credits": 11, "requirementGroup": 14, "departments": 15},
    "CS370", "CS", "370", "Computer Science Practicum", "<p>Software &amp; teams.</p>",
    {"creditHours": 12}, {"min": 13, "max": 13}, 3, "001671", [],
])
GROUPS = {"001671": {"catalogDisplayName":
                     "CS 253 or equivalent transfer credit as prerequisite."}}


def test_decode_nuxt_payload_resolves_indexes():
    payload = decode_nuxt_payload(CS370_PAGE)
    assert payload["data"]["course-0047402-undefined"]["course"]["code"] == "CS370"


def test_course_page_to_importer_record_and_parse():
    course = course_from_page(CS370_PAGE)
    assert "departments" not in course
    record, reason = course_record(course, GROUPS)
    assert reason is None
    assert record == {
        "code": "CS 370",
        "title": "Computer Science Practicum",
        "description": "Software & teams.",
        "credits": "3",
        "requisites": "CS 253 or equivalent transfer credit as prerequisite.",
    }
    tree = parse_requisites(record["requisites"]).trees["prerequisite"]
    assert tree == AnyOf((CourseRef("CS 253"), Condition("equivalent transfer credit")))


def test_course_record_preserves_explicit_crosslist_only():
    course = {
        "subjectCode": "AAS", "courseNumber": "325", "longName": "Black Love",
        "description": "Religion and race.",
        "crossListedCourses": [
            {"name": "REL325 Black Love"},
            {"name": "not a code"},
        ],
    }
    record, reason = course_record(course, {})
    assert reason is None
    assert record["crosslisted"] == ["REL 325"]
    # A similar title with no explicit relation is not an identity.
    plain, _ = course_record({"subjectCode": "REL", "courseNumber": "330",
                              "longName": "Black Love"}, {})
    assert "crosslisted" not in plain


def test_missing_requirement_group_is_a_failure_not_an_empty_prereq():
    course = course_from_page(CS370_PAGE)
    record, reason = course_record(course, {})
    assert record is None and "001671" in reason


def test_credits_text_ranges():
    assert credits_text({"credits": {"creditHours": {"min": 1, "max": 4}}}) == "1-4"
    assert credits_text({"credits": {"creditHours": {"min": 1.5, "max": 1.5}}}) == "1.5"
    assert credits_text({}) is None


def test_sitemap_paths_keeps_ids_only():
    xml = ("<urlset><url><loc>https://x.coursedog.com/courses/0047402</loc></url>"
           "<url><loc>https://x.coursedog.com/courses</loc></url>"
           "<url><loc>https://x.coursedog.com/programs/COMPSCIBS</loc></url></urlset>")
    assert sitemap_paths(xml, "courses") == ["0047402"]
    assert sitemap_paths(xml, "programs") == ["COMPSCIBS"]


def test_scrape_end_to_end_with_cache(tmp_path):
    sitemap = ("<urlset><url><loc>https://x/courses/0047402</loc></url>"
               "<url><loc>https://x/courses/0000001</loc></url></urlset>")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        if path == "/courses/0047402":
            return httpx.Response(200, text=CS370_PAGE)
        if path.startswith("/api/v1/emory_peoplesoft_direct/requirementGroups/001671"):
            return httpx.Response(200, json={"data": GROUPS})
        return httpx.Response(404)

    client = scrape_catalog.make_client(transport=httpx.MockTransport(handler))
    raw = tmp_path / "raw"
    records, failures = scrape_catalog.scrape(client, raw_dir=raw, delay=0,
                                              sleep=lambda _: None)
    assert "STEM-Advisor" in client.headers["User-Agent"]
    assert [f["id"] for f in failures] == ["0000001"]

    out = tmp_path / "atlas.jsonl"
    write_jsonl(records, out)
    assert [c.code for c in load_atlas(out)] == ["CS 370"]

    # Offline rebuild needs no client and matches.
    again, _ = scrape_catalog.scrape(None, raw_dir=raw, delay=0, sleep=lambda _: None)
    assert again == records


def test_get_honors_retry_after_and_stops_on_refusal():
    waits = []
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}),
                      httpx.Response(200, text="ok")])
    client = scrape_catalog.make_client(
        transport=httpx.MockTransport(lambda request: next(responses)))
    assert scrape_catalog.get(client, "https://x/", sleep=waits.append).text == "ok"
    assert waits == [7.0]

    refused = scrape_catalog.make_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    with pytest.raises(scrape_catalog.FetchError, match="401"):
        scrape_catalog.get(refused, "https://x/", sleep=waits.append)


def test_catalog_scrape_refresh_cache_reloads_course_and_requirement_data(tmp_path):
    raw = tmp_path / "raw"
    course_cache = raw / "courses" / "0047402.json"
    course_cache.parent.mkdir(parents=True)
    course_cache.write_text(json.dumps({"subjectCode": "OLD", "code": "OLD 1"}))
    (raw / "requirement_groups.json").write_text(json.dumps({"001671": {"descriptionLong": "old"}}))
    (raw / "sitemap.xml").write_text(
        "<urlset><url><loc>https://x/courses/0047402</loc></url></urlset>"
    )
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=(
                "<urlset><url><loc>https://x/courses/0047402</loc></url></urlset>"
            ))
        if request.url.path == "/courses/0047402":
            return httpx.Response(200, text=CS370_PAGE)
        if "requirementGroups" in request.url.path:
            return httpx.Response(200, json={"data": GROUPS})
        return httpx.Response(404)

    client = scrape_catalog.make_client(transport=httpx.MockTransport(handler))
    records, failures = scrape_catalog.scrape(
        client, raw_dir=raw, delay=0, refresh_cache=True, sleep=lambda _: None
    )
    assert not failures
    assert [record["code"] for record in records] == ["CS 370"]
    assert "/sitemap.xml" in requests
    assert "/courses/0047402" in requests
    assert any("requirementGroups" in path for path in requests)


def test_cached_catalog_json_must_be_an_object(tmp_path):
    cached = tmp_path / "cache.json"
    cached.write_text("[]", encoding="utf-8")
    with pytest.raises(scrape_catalog.FetchError, match="not an object"):
        scrape_catalog._read_json(cached)


@pytest.mark.parametrize("scenario", ["failed", "empty", "subject", "limit", "complete", "trial"])
def test_catalog_publication_preserves_complete_snapshot(tmp_path, monkeypatch, capsys, scenario):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    atlas = tmp_path / "atlas" / "emory_college.jsonl"
    atlas.parent.mkdir()
    original = json.dumps({"code": "MATH 111", "title": "Calculus I"}) + "\n"
    atlas.write_text(original, encoding="utf-8")
    records = [{"code": "CS 370", "title": "Computer Science Practicum"}]
    failures = [{"stage": "course", "id": "missing", "error": "not in cache"}]
    monkeypatch.setattr(scrape_catalog, "scrape", lambda *args, **kwargs: (
        [] if scenario == "empty" else records, failures if scenario == "failed" else []))
    options = {
        "subject": ["--subjects", "CS"], "limit": ["--limit", "1"],
        "trial": ["--limit", "1", "--out", str(tmp_path / "trial.jsonl")],
    }.get(scenario, [])
    monkeypatch.setattr(sys, "argv", ["scrape_catalog", "--offline", *options])
    if scenario in {"complete", "trial"}:
        scrape_catalog.main()
    else:
        with pytest.raises(SystemExit) as exc:
            scrape_catalog.main()
        assert exc.value.code == 2
    if scenario == "complete":
        assert [c.code for c in load_atlas(atlas)] == ["CS 370"]
    else:
        assert atlas.read_text(encoding="utf-8") == original
    if scenario == "trial":
        assert [c.code for c in load_atlas(tmp_path / "trial.jsonl")] == ["CS 370"]
    if scenario != "complete":
        assert "next: python -m scripts.import_atlas" not in capsys.readouterr().out


DEPT_PAGE = """
<html><body><nav>Home Majors</nav>
<main><div id="main-sidebar"><ul><li>Sidebar link</li></ul></div>
<div id="main-column"><h1>CS BS</h1>
<table><tr><td><strong>Requirement</strong></td><td>Course</td></tr>
<tr><td>Math 111</td><td>Calculus I</td></tr>
<tr><td>One course chosen from:</td><td><p>CS 325: AI</p><p>CS 334: ML</p></td></tr></table>
<h5>GPA</h5><ul><li>Letter grade only.</li></ul></div></main>
<footer>Contact us</footer></body></html>
"""


def test_page_to_markdown_keeps_content_column_only():
    markdown = page_to_markdown(DEPT_PAGE)
    assert markdown.startswith("# CS BS")
    assert "| Math 111 | Calculus I |" in markdown
    assert "| One course chosen from: | CS 325: AI CS 334: ML |" in markdown
    assert "- Letter grade only." in markdown
    for dropped in ("Sidebar link", "Home Majors", "Contact us"):
        assert dropped not in markdown


def test_scrape_programs_writes_markdown_with_provenance(tmp_path):
    pdf = b"%PDF-1.4 fake"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=pdf,
                                  headers={"content-type": "application/pdf"})
        filler = "<p>" + "Majors must earn a C or better in every course. " * 5 + "</p>"
        return httpx.Response(200, text=DEPT_PAGE.replace("</h1>", "</h1>" + filler))

    client = scrape_catalog.make_client(transport=httpx.MockTransport(handler))
    pages = [{"url": "https://computerscience.emory.edu/ug/cs-bs.html", "programs": ["CSBS"]},
             {"url": "https://physics.emory.edu/documents/table.pdf", "programs": ["CSBS"]}]
    department_dir = tmp_path / "departments"
    written, failures = scrape_programs.scrape(
        client, names={"CSBS": "Computer Science BS"}, pages=pages, out_dir=department_dir,
        delay=0, retrieved="2026-10-08", sleep=lambda _: None)
    assert failures == []
    md, saved_pdf = written
    text = md.read_text(encoding="utf-8")
    assert "Source: https://computerscience.emory.edu/ug/cs-bs.html" in text
    assert "Computer Science BS (CSBS)" in text
    assert "Retrieved:" not in text
    assert saved_pdf.read_bytes() == pdf
    manifest = json.loads(
        (tmp_path / "ingest-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["sources"]["departments/computerscience-ug-cs-bs.md"]["origin_key"] == (
        "programs:department:computerscience-ug-cs-bs"
    )
    assert manifest["sources"]["departments/computerscience-ug-cs-bs.md"]["acquired_at"] == "2026-10-08"
    stable_content = md.read_bytes()
    scrape_programs.scrape(
        client, names={"CSBS": "Computer Science BS"}, pages=pages, out_dir=department_dir,
        delay=0, retrieved="2026-10-09", sleep=lambda _: None)
    assert md.read_bytes() == stable_content
    refreshed = json.loads((tmp_path / "ingest-manifest.json").read_text(encoding="utf-8"))
    assert refreshed["sources"]["departments/computerscience-ug-cs-bs.md"]["acquired_at"] == "2026-10-09"
    assert refreshed["sources"]["departments/computerscience-ug-cs-bs.md"]["content_sha256"] == (
        hashlib.sha256(stable_content).hexdigest()
    )


def test_bot_challenge_stops_the_run_and_keeps_progress(tmp_path):
    sitemap = ("<urlset><url><loc>https://x/courses/0047402</loc></url>"
               "<url><loc>https://x/courses/0047403</loc></url>"
               "<url><loc>https://x/courses/0047404</loc></url></urlset>")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        if request.url.path == "/courses/0047402":
            return httpx.Response(200, text=CS370_PAGE)
        if "requirementGroups" in request.url.path:
            return httpx.Response(200, json={"data": GROUPS})
        return httpx.Response(405, headers={"x-amzn-waf-action": "captcha"})

    client = scrape_catalog.make_client(transport=httpx.MockTransport(handler))
    records, failures = scrape_catalog.scrape(client, raw_dir=tmp_path, delay=0,
                                              sleep=lambda _: None)
    assert [r["code"] for r in records] == ["CS 370"]
    assert "/courses/0047404" not in calls  # stopped at the first challenge
    assert "bot challenge" in failures[-1]["error"]


def test_local_catalog_preparation_writes_bound_requirements_without_department_config(
    tmp_path, monkeypatch,
):
    pdf = tmp_path / "catalog.pdf"
    pdf.write_bytes(make_pdf([
        "2026-2027 Emory College",
        "CHEMISTRY (BA)",
        "DEGREE AWARDED BA",
        "Requirements",
        "CHEM 150",
    ]))
    staging = tmp_path / "staging"
    monkeypatch.setenv("DATA_DIR", str(staging))
    url = "https://example.test/catalog.pdf"
    monkeypatch.setattr(sys, "argv", [
        "scrape_programs", "--pdf", str(pdf), "--source-url", url,
        "--config", str(tmp_path / "absent-department-config.toml"),
    ])

    scrape_programs.main()

    root = staging / "programs"
    records = [json.loads(line) for line in (root / "programs.jsonl").read_text().splitlines()]
    assert [record["title"] for record in records] == ["CHEMISTRY (BA)"]
    assert "CHEM 150" in records[0]["items"][-1]["courses"]
    identity = records[0]["source"]
    source = root / identity["manifest_path"]
    manifest = json.loads((root / "ingest-manifest.json").read_text())
    entry = manifest["sources"][identity["manifest_path"]]
    assert entry["source_url"] == url
    assert entry["provenance"]["title"] == records[0]["title"]
    assert entry["provenance"]["applicability"]["catalog_edition"] == [records[0]["catalog"]]
    assert identity["content_sha256"] == entry["content_sha256"] == (
        hashlib.sha256(source.read_bytes()).hexdigest()
    )


def test_catalog_pdf_splits_policies_and_programs():
    from app.programs.catalog_pdf import split_catalog

    pages = [
        "Emory University\n2026-2027 Emory College\n10 ABOUT EMORY COLLEGE\n40 CS (BS)\n",
        ("ABOUT EMORY COLLEGE\nFounded in 1836.\nDepartment of Veterans Affairs Pending Payment Rights\n"
         "Emory University\n10 / 158\n"),
        ("EMORY COLLEGE OF ARTS AND SCIENCES PROGRAMS\nComputer Science (BS)\n"
         "DEGREE AWARDED BS\nRequirements\n\u2022 CS 170\nEmory University\n11 / 158\n"),
        "MATHEMATICS (BA)\nDEGREE AWARDED BA\n\u2022 Math 111\n",
    ]
    policies, programs, departments = split_catalog(pages)
    assert policies.text == ("ABOUT EMORY COLLEGE\nFounded in 1836.\n"
                             "Department of Veterans Affairs Pending Payment Rights\n")
    assert [(p.title, p.first_page, p.last_page) for p in programs] == [
        ("Computer Science (BS)", 3, 3), ("MATHEMATICS (BA)", 4, 4)]
    assert "11 / 158" not in programs[0].text and "\u2022 CS 170" in programs[0].text
    assert departments == []


def test_catalog_pdf_tolerates_dropped_degree_awarded_typos():
    """EMO-003: the published PDF misspells DEGREE AWARDED for 3 programs."""
    from app.programs.catalog_pdf import split_catalog

    pages = [
        ("EMORY COLLEGE OF ARTS AND SCIENCES PROGRAMS\nChemistry (BA)\n"
         "DEZGREE AWRDED BA\nRequirements\n\u2022 CHEM 150\n"
         "Classics (BA)\nDEGREE AWRDED BA\n\u2022 GREK 101\n"
         "Data Science (Minor)\nDEGREE AWARDED N/A\n\u2022 QTM 100\n"),
    ]
    _policies, programs, _departments = split_catalog(pages)
    assert [p.title for p in programs] == [
        "Chemistry (BA)", "Classics (BA)", "Data Science (Minor)"]


def test_catalog_pdf_stops_the_last_program_at_a_department_section():
    from app.programs.catalog_pdf import split_catalog

    pages = [
        ("EMORY COLLEGE OF ARTS AND SCIENCES PROGRAMS\nWriting (Minor)\n"
         "DEGREE AWARDED N/A\nRequirements\n\u2022 ENGRD 219\n"
         "African American Studies Department\nAFRICAN AMERICAN STUDIES\n"
         "A department description.\nDepartment of History\nHISTORY\n"
         "Another description.\n"),
    ]
    policies, programs, departments = split_catalog(pages)
    assert policies is None
    assert len(programs) == 1
    assert programs[0].title == "Writing (Minor)"
    assert "Department" not in programs[0].text
    assert [d.title for d in departments] == [
        "African American Studies Department", "Department of History"]
    assert "ENGRD 219" not in "".join(d.text for d in departments)


def test_mark_headings_only_touches_caps_heading_lines():
    from app.programs.catalog_pdf import mark_headings

    text = "ACADEMIC STANDING\nStudents must keep a 2.0.\nCS 170\nGPA\n"
    assert mark_headings(text) == "## ACADEMIC STANDING\nStudents must keep a 2.0.\nCS 170\nGPA\n"


def _reviewed_advising_batch(tmp_path):
    body = b"Main content\n\n# Program\n\n| Course | Choice |\n|---|---|\n| CS 170 | or CS 171 |\n"
    wrapper = (b"# acquired-name\n\nSource: https://example.test/program\n"
               b"Retrieved: 2026-10-15 (ET)\n\n---\n\n")
    (tmp_path / "page.md").write_bytes(wrapper + body)
    (tmp_path / "sheet.pdf").write_bytes(make_pdf(["Program requirements", "CS 170 or CS 171"]))
    (tmp_path / "held.md").write_bytes(b"Needs review")
    records = []
    for filename, normalization in (("page.md", "acquisition_wrapper"),
                                    ("sheet.pdf", "preserve_pdf")):
        records.append({
            "id": filename, "disposition": "include", "original_path": filename,
            "original_sha256": hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest(),
            "reason": "Reviewed local institutional publication, applicability unknown.",
            "origin_key": f"advising:test:{filename}", "canonical_filename": filename,
            "normalization": normalization,
            "provenance": {
                "source_category": "institutional_publication", "publisher": "Example Office",
                "source_url": "https://example.test/program", "title": "Program requirements",
                "institution": "Example University", "issuing_unit": "Example Office",
                "document_roles": ["requirements"], "applicability": {"program": ["Example"]},
                "applicability_status": "unknown",
            },
        })
    records.append({
        "id": "held", "disposition": "hold", "original_path": "held.md",
        "original_sha256": hashlib.sha256(b"Needs review").hexdigest(),
        "reason": "Table interpretation requires review.",
    })
    return {"schema_version": 1, "acquired_at": "2026-10-15", "document": records}, body


def test_reviewed_advising_preparation_preserves_evidence_and_ignores_acquisition_churn(tmp_path):
    registry, body = _reviewed_advising_batch(tmp_path)
    first = prepare_documents(registry, input_root=tmp_path, registry_sha256="a" * 64)
    assert [doc.content for doc in first.documents] == [body, (tmp_path / "sheet.pdf").read_bytes()]
    manifest = first.manifest()["sources"]
    assert set(manifest) == {"documents/page.md", "documents/sheet.pdf"}
    assert manifest["documents/page.md"]["content_sha256"] == hashlib.sha256(body).hexdigest()
    item = first.lineage["documents"][0]
    original = (tmp_path / "page.md").read_bytes()
    assert original[item["canonical_byte_start"]:] == body
    assert item["removed_wrapper"].encode() + body == original
    registry["acquired_at"] = "2026-10-16"
    updated = original.replace(b"Retrieved: 2026-10-15", b"Retrieved: 2026-10-16")
    (tmp_path / "page.md").write_bytes(updated)
    registry["document"][0]["original_sha256"] = hashlib.sha256(updated).hexdigest()
    second = prepare_documents(registry, input_root=tmp_path, registry_sha256="b" * 64)
    assert [doc.content for doc in second.documents] == [doc.content for doc in first.documents]
    assert second.manifest()["sources"]["documents/page.md"]["content_sha256"] == (
        manifest["documents/page.md"]["content_sha256"]
    )
    assert second.manifest()["sources"]["documents/page.md"]["acquired_at"] == "2026-10-16"


@pytest.mark.parametrize("change,error", [
    ("changed_original", "reviewed SHA-256"),
    ("path_escape", "within the input directory"),
    ("duplicate_origin", "duplicate included origin_key"),
    ("duplicate_filename", "duplicate canonical_filename"),
    ("wrong_wrapper_url", "wrapper disagrees with registry"),
    ("historical_include", "historical applicability cannot be included"),
])
def test_advising_preparation_rejects_unreviewed_identity_or_applicability(tmp_path, change, error):
    registry, _body = _reviewed_advising_batch(tmp_path)
    page, pdf, _held = registry["document"]
    if change == "changed_original":
        (tmp_path / "held.md").write_bytes(b"Replaced after review")
    elif change == "path_escape":
        page["original_path"] = "../outside.md"
    elif change == "duplicate_origin":
        pdf["origin_key"] = page["origin_key"]
    elif change == "duplicate_filename":
        pdf["canonical_filename"] = page["canonical_filename"]
    elif change == "wrong_wrapper_url":
        page["provenance"]["source_url"] = "https://example.test/other-program"
    elif change == "historical_include":
        page["provenance"]["applicability_status"] = "historical"
    with pytest.raises(ValueError, match=error):
        prepare_documents(registry, input_root=tmp_path, registry_sha256="a" * 64)
