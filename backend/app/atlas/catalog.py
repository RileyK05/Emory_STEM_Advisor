"""Turn Emory's public college catalog (Coursedog) into atlas JSONL records. No network.

The scraper (`scripts/scrape_catalog.py`) fetches; this module only reshapes.
Each course page embeds its course record in a Nuxt payload; the requisite
text lives in a separate "requirement group" looked up by id. That text is
passed through verbatim (HTML stripped) so the deterministic parser in
`requisites.py` is the only thing that reads its structure.
"""

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from app.atlas.records import normalize_code
from app.util.atomic import write_text_atomic

_NUXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef"}
_EMPTY = {"EmptyRef", "EmptyShallowRef", "undefined", "null", "NaN", "Map"}
_BLOCK_TAGS = {"br", "p", "div", "li", "ul", "ol", "tr", "td", "h1", "h2", "h3", "h4"}
_SPACE_RE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data) -> None:
        self.parts.append(data)


def html_to_text(value) -> str:
    """Plain text from a catalog HTML fragment, whitespace collapsed."""
    if value is None:
        return ""
    extractor = _TextExtractor()
    extractor.feed(str(value))
    extractor.close()
    return _SPACE_RE.sub(" ", html.unescape("".join(extractor.parts))).strip()


def decode_nuxt_payload(page: str):
    """Decode a Nuxt 3 `__NUXT_DATA__` payload (devalue: values refer by index)."""
    match = _NUXT_DATA_RE.search(page)
    if match is None:
        raise ValueError("page has no __NUXT_DATA__ payload")
    table = json.loads(match.group(1))
    memo: dict[int, object] = {}

    def hydrate(index):
        if not isinstance(index, int) or index < 0:
            return None
        if index in memo:
            return memo[index]
        value = table[index]
        if isinstance(value, list):
            tag = value[0] if value and isinstance(value[0], str) else None
            if tag in _WRAPPERS:
                memo[index] = hydrate(value[1]) if len(value) > 1 else None
                return memo[index]
            if tag in _EMPTY:
                return None
            if tag == "Date":
                return value[1]
            if tag == "Set":
                return [hydrate(item) for item in value[1:]]
            out: list = []
            memo[index] = out
            out.extend(hydrate(item) for item in value)
            return out
        if isinstance(value, dict):
            obj: dict = {}
            memo[index] = obj
            for key, item in value.items():
                obj[key] = hydrate(item)
            return obj
        return value

    return hydrate(0)


def course_from_page(page: str) -> dict | None:
    """The course record embedded in a catalog course page, or None."""
    payload = decode_nuxt_payload(page)
    data = (payload or {}).get("data") or {}
    for value in data.values():
        if isinstance(value, dict) and isinstance(value.get("course"), dict):
            course = dict(value["course"])
            course.pop("departments", None)  # large and unused
            return course
    return None


def sitemap_paths(xml: str, section: str) -> list[str]:
    """Ids under /<section>/ in the sitemap, e.g. course ids for "courses".

    The sitemap's host is Coursedog's, not the public catalog's, so only the
    path is kept.
    """
    ids = []
    for loc in _LOC_RE.findall(xml):
        parts = urlparse(loc).path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == section and parts[1]:
            ids.append(parts[1])
    return sorted(set(ids))


def credits_text(course: dict) -> str | None:
    hours = ((course.get("credits") or {}).get("creditHours")) or {}
    low, high = hours.get("min"), hours.get("max")
    if low is None and high is None:
        return None
    low = high if low is None else low
    high = low if high is None else high
    if low == high:
        return _number(low)
    return f"{_number(low)}-{_number(high)}"


def _number(value) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def requisite_text(group: dict | None) -> str | None:
    if not group:
        return None
    text = html_to_text(group.get("catalogDisplayName") or group.get("descriptionLong"))
    return text or None


def crosslisted_codes(course: dict) -> list[str]:
    """Official cross-list codes embedded in the course record (no network).

    Each relation's `name` begins with the partner code (for example
    "REL325 Black Love"); only that explicit relation is used, never a title.
    """
    codes: list[str] = []
    for relation in course.get("crossListedCourses") or []:
        name = str(relation.get("name") or "").strip()
        if not name:
            continue
        code = normalize_code(name.split(" ", 1)[0])
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def course_record(course: dict, groups: dict[str, dict]) -> tuple[dict | None, str | None]:
    """An `AtlasCourse`-shaped dict, or (None, reason)."""
    subject = str(course.get("subjectCode") or "").strip()
    number = str(course.get("courseNumber") or "").strip()
    raw_code = f"{subject} {number}" if subject and number else str(course.get("code") or "")
    code = normalize_code(raw_code)
    if code is None:
        return None, f"code does not fit the atlas shape: {raw_code!r}"
    title = html_to_text(course.get("longName") or course.get("name"))
    if not title:
        return None, "missing title"
    group_id = str(course.get("requirementGroup") or "")
    if group_id and group_id not in groups:
        return None, f"requirement group {group_id} not found"
    record = {
        "code": code,
        "title": title,
        "description": html_to_text(course.get("description")),
        "credits": credits_text(course),
        "requisites": requisite_text(groups.get(group_id)) if group_id else None,
    }
    crosslisted = [alias for alias in crosslisted_codes(course) if alias != code]
    if crosslisted:
        record["crosslisted"] = crosslisted
    return record, None


def write_jsonl(records: list[dict], path: Path) -> None:
    """Write records in code order, one per line, as the importer reads them."""
    ordered = sorted(records, key=lambda r: r["code"])
    body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in ordered)
    write_text_atomic(path, body)
