"""Fetch program requirements into documents for `ingest`.

Usage (from backend/):
    python -m scripts.scrape_programs [--config configs/programs.toml] [--skip-departments]
    python -m scripts.scrape_programs --pdf PATH --source-url URL

The local --pdf mode prepares the catalog only, without a network client or
department configuration. Set DATA_DIR to a separate directory to stage output.

Two sources, each written with a provenance header:

1. The official catalog PDF (catalog of record). The catalog website shows no
   program requirements, but the PDF it generates does. Its current URL comes
   from the same public endpoint the catalog site calls; it is split into one
   Markdown document per program (plus one for academic policies) under
   DATA_DIR/programs/catalog/.
2. Department pages listed in `configs/programs.toml` (hand-picked, verified),
   which add advising notes the catalog omits. HTML becomes Markdown; PDFs are
   saved as-is, under DATA_DIR/programs/departments/.

Then:
    python -m scripts.ingest --collection default --origin-manifest \\
        ../data/programs/ingest-manifest.json ../data/programs/catalog/* \\
        ../data/programs/departments/*
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import time
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from pypdf import PdfReader

from app.config import load_settings
from app.programs.catalog_pdf import CatalogSection, mark_headings, split_catalog
from app.programs.pages import page_to_markdown
from app.programs.requirements import parse_program, to_record
from app.util.atomic import write_text_atomic
from scripts.scrape_catalog import API, SCHOOL, FetchError, get, make_client

CATALOG_ID = "FUoE83Jq1SyDx5rJvewc"  # college.catalog.emory.edu's active catalog
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "programs.toml"
_BULLET_RE = re.compile(r"^[•●]\s*", re.MULTILINE)
_SUB_BULLET_RE = re.compile(r"^[◦○]\s*", re.MULTILINE)
_YEAR_RE = re.compile(r"\b(\d{4}-\d{4})\b")


def load_config(path: Path) -> tuple[dict[str, str], list[dict]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    names: dict[str, str] = data["programs"]
    pages: list[dict] = data["page"]
    for page in pages:
        unknown = [code for code in page["programs"] if code not in names]
        if unknown:
            raise ValueError(f"{page['url']}: unknown program codes {unknown}")
    covered = {code for page in pages for code in page["programs"]}
    missing = sorted(set(names) - covered)
    if missing:
        raise ValueError(f"programs with no page: {missing}")
    return names, pages


_PUBLISHER = "Emory College of Arts and Sciences"


def slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.split(".")[0]
    path = re.sub(r"\.(html?|pdf)$", "", unquote(parsed.path), flags=re.IGNORECASE)
    path = re.sub(r"/index$", "", path)
    return re.sub(r"[^a-z0-9]+", "-", f"{host}-{path}".lower()).strip("-")[:120]


def department_origin_key(url: str) -> str:
    return f"programs:department:{slug(url)}"


def catalog_origin_key(title: str) -> str:
    return f"programs:catalog:{slug(title)}"


def render(markdown: str, *, url: str, programs: list[str], names: dict[str, str]) -> str:
    covered = ", ".join(f"{names[code]} ({code})" for code in programs)
    header = (
        f"# Program requirements: {covered}\n\n"
        f"Source: {url}\n"
        f"Programs: {covered}\n\n"
        "Copied from the department website; the catalog of record may differ.\n\n"
        "---\n\n"
    )
    return header + markdown


def catalog_pdf_url(client: httpx.Client, catalog_id: str, *, sleep=time.sleep) -> str:
    report = get(client, f"{API}/{SCHOOL}/pdf-reports/catalogs/{catalog_id}", sleep=sleep).json()
    if report.get("status") != "succeed" or not report.get("url"):
        raise FetchError(f"no generated PDF for catalog {catalog_id}: {report}")
    return report["url"]


def render_section(section: CatalogSection, *, catalog: str, catalog_id: str) -> str:
    pages = (f"page {section.first_page}" if section.first_page == section.last_page
             else f"pages {section.first_page}-{section.last_page}")
    body = _SUB_BULLET_RE.sub("  - ", _BULLET_RE.sub("- ", section.text))
    return (
        f"# {section.title}\n\n"
        f"Source: {catalog}, official catalog PDF, {pages}\n"
        "URL: https://college.catalog.emory.edu/\n"
        f"Catalog ID: {catalog_id}\n"
        "\n"
        "---\n\n"
        f"{body}"
    )


def _record_ingest_manifest(root: Path, records: dict[str, dict]) -> None:
    path = root / "ingest-manifest.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    sources = existing.setdefault("sources", {})
    sources.update(records)
    write_text_atomic(path, json.dumps(existing, indent=2, sort_keys=True) + "\n")


def dedupe_programs(programs: list[CatalogSection]) -> list[CatalogSection]:
    """The PDF prints some programs twice (e.g. Arabic (Minor)); keep the first.
    Copies are compared as parsed requirements (labels like "DEPARTMENT CONTACT"
    vs "CONTACTS" differ); a repeated title with different requirements is a
    real conflict, so it is an error."""
    first: dict[str, CatalogSection] = {}
    for section in programs:
        seen = first.get(section.title)
        if seen is None:
            first[section.title] = section
        elif (to_record(parse_program(seen.title, seen.text), catalog="", source_url="",
                        first_page=0, last_page=0)
              != to_record(parse_program(section.title, section.text), catalog="",
                           source_url="", first_page=0, last_page=0)):
            raise ValueError(f"catalog has two different programs titled {section.title!r} "
                             f"(pages {seen.first_page} and {section.first_page})")
    return list(first.values())


def write_programs_jsonl(programs: list[CatalogSection], *, catalog: str, url: str,
                         path: Path, identities: dict[str, dict] | None = None) -> int:
    """Parsed requirements, one program per line, for `scripts.import_programs`.

    `identities` maps a program title to the exact-SHA manifest identity of the
    catalog file written for it. The importer binds a program to its indexed
    source only through that identity, never through a title or filename match.
    """
    records = []
    for section in programs:
        record = to_record(parse_program(section.title, section.text), catalog=catalog,
                           source_url=url, first_page=section.first_page,
                           last_page=section.last_page)
        if identities and section.title in identities:
            record["source"] = identities[section.title]
        records.append(record)
    body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    write_text_atomic(path, body)
    return len(records)


def write_catalog(pdf: bytes, *, url: str, catalog_id: str, out_dir: Path,
                  retrieved: str) -> list[Path]:
    pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages]
    year = _YEAR_RE.search(pages[0] if pages else "")
    catalog = "Emory College of Arts and Sciences Catalog" + (f" {year.group(1)}" if year else "")
    policies, programs, departments = split_catalog(pages)
    programs = dedupe_programs(programs)
    if not programs:
        raise FetchError("catalog PDF has no recognizable programs")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    manifest_records: dict[str, dict] = {}
    identities: dict[str, dict] = {}
    program_titles = {section.title for section in programs}
    if policies:
        policies = CatalogSection(policies.title, mark_headings(policies.text),
                                  policies.first_page, policies.last_page)
    for section in ([policies] if policies else []) + programs + departments:
        path = out_dir / f"{re.sub(r'[^a-z0-9]+', '-', section.title.lower()).strip('-')}.md"
        if path in written:
            path = path.with_name(f"{path.stem}-p{section.first_page}.md")
        write_text_atomic(path, render_section(section, catalog=catalog, catalog_id=catalog_id))
        written.append(path)
        relative = path.relative_to(out_dir.parent).as_posix()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        # The catalog of record is the official PDF; its policies section is policy.
        category = ("institutional_policy" if policies and section is policies
                    else "institutional_catalog")
        manifest_records[relative] = {
            "origin_key": catalog_origin_key(section.title),
            "acquired_at": retrieved,
            "source_url": url,
            "content_sha256": sha,
            "provenance": {"source_category": category, "publisher": _PUBLISHER,
                           "edition": catalog, "title": section.title,
                           "institution": "Emory University", "issuing_unit": _PUBLISHER,
                           "document_roles": [
                               "academic_policy" if category == "institutional_policy" else
                               "program_requirements" if section.title in program_titles else
                               "department_overview"],
                           "applicability": {"school": [_PUBLISHER],
                                             "catalog_edition": [catalog]},
                           "applicability_status": "reviewed_default"},
        }
        if section.title in program_titles and section.title not in identities:
            identities[section.title] = {
                "manifest_path": relative, "origin_key": catalog_origin_key(section.title),
                "content_sha256": sha, "catalog_id": catalog_id,
            }
    _record_ingest_manifest(out_dir.parent, manifest_records)
    write_programs_jsonl(programs, catalog=catalog, url=url,
                         path=out_dir.parent / "programs.jsonl", identities=identities)
    return written


def scrape(client: httpx.Client, *, names: dict[str, str], pages: list[dict], out_dir: Path,
           delay: float, retrieved: str, sleep=time.sleep) -> tuple[list[Path], list[dict]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    failures: list[dict] = []
    manifest_records: dict[str, dict] = {}
    for page in pages:
        url = page["url"]
        try:
            response = get(client, url, sleep=sleep)
        except FetchError as exc:
            failures.append({"url": url, "error": str(exc)})
            continue
        finally:
            sleep(delay)
        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            path = out_dir / f"{slug(url)}.pdf"
            path.write_bytes(response.content)
        else:
            markdown = page_to_markdown(response.text)
            if len(markdown) < 200:
                failures.append({"url": url, "error": "page has almost no content"})
                continue
            path = out_dir / f"{slug(url)}.md"
            path.write_text(
                render(markdown, url=url, programs=page["programs"], names=names),
                encoding="utf-8", newline="\n",
            )
        written.append(path)
        manifest_records[path.relative_to(out_dir.parent).as_posix()] = {
            "origin_key": department_origin_key(url),
            "acquired_at": retrieved,
            "source_url": url,
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            # Department websites supplement the catalog and can be stale.
            "provenance": {"source_category": "institutional_department",
                           "publisher": _PUBLISHER},
        }
    _record_ingest_manifest(out_dir.parent, manifest_records)
    return written, failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--catalog-id", default=CATALOG_ID)
    parser.add_argument("--pdf", type=Path, help="prepare a local catalog PDF without network access")
    parser.add_argument("--source-url", help="original public URL of the local catalog PDF")
    parser.add_argument("--skip-departments", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    if args.pdf is not None:
        if not args.pdf.is_file():
            parser.error("--pdf must name an existing file")
        if not args.source_url or not re.fullmatch(r"https?://[^\s<>\"']+", args.source_url):
            parser.error("--pdf requires an http(s) --source-url")
    elif args.source_url is not None:
        parser.error("--source-url requires --pdf")

    root = load_settings().data_dir / "programs"
    retrieved = dt.datetime.now(dt.UTC).date().isoformat()
    if args.pdf is not None:
        catalog_docs = write_catalog(args.pdf.read_bytes(), url=args.source_url,
                                     catalog_id=args.catalog_id, out_dir=root / "catalog",
                                     retrieved=retrieved)
    else:
        names, pages = load_config(args.config)
        with make_client() as client:
            url = catalog_pdf_url(client, args.catalog_id)
            catalog_docs = write_catalog(get(client, url).content, url=url,
                                         catalog_id=args.catalog_id, out_dir=root / "catalog",
                                         retrieved=retrieved)
            if not args.skip_departments:
                written, failures = scrape(client, names=names, pages=pages,
                                           out_dir=root / "departments", delay=args.delay,
                                           retrieved=retrieved)
                print(f"wrote {len(written)} department documents to {root / 'departments'}")
                for failure in failures:
                    print(f"  !! {failure['url']}: {failure['error']}")
    print(f"wrote {len(catalog_docs)} catalog documents to {root / 'catalog'}")
    print(f"wrote parsed requirements to {root / 'programs.jsonl'}")
    inputs = f"{root}/catalog/*"
    if args.pdf is None and not args.skip_departments:
        inputs += f" {root}/departments/*"
    print(f"next: python -m scripts.ingest --collection default --origin-manifest "
          f"{root}/ingest-manifest.json {inputs}")
    print(f"      python -m scripts.import_programs --collection default {root}/programs.jsonl")


if __name__ == "__main__":
    main()
