"""Catalog and program-page scrapers. Mock transports only; no network."""

import hashlib
import json
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
from app.atlas.records import load_atlas
from app.atlas.requisites import AnyOf, Condition, CourseRef, parse_requisites
from app.programs.pages import page_to_markdown
from scripts import scrape_catalog, scrape_programs

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


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


def test_programs_config_covers_every_program():
    names, pages = scrape_programs.load_config(CONFIGS / "programs.toml")
    assert {"COMPSCIBS", "MATHBS", "QSSBS", "PHYSICSBS", "BIOLOGYBS"} <= set(names)
    assert all(page["url"].split("/")[2].endswith("emory.edu") for page in pages)


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
