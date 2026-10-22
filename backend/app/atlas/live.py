"""Read immutable Atlas browser captures into staging-only typed records.

This module does not normalize course identities or write to the atlas/database.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlparse


@dataclass(frozen=True)
class Section:
    crn: str
    section: str | None
    section_type: str | None
    campus: str | None
    meeting: str | None
    instructor: str | None
    status: str | None
    key: str
    group: str
    matched: str
    srcdb: str


@dataclass(frozen=True)
class DetailRecord:
    code: str
    title: str
    description: str
    restrictions: str | None
    credits: str | None
    class_number: str | None
    is_grouped: bool
    career: str
    term: str
    observed_at: str
    evidence_hash: str
    sections: tuple[Section, ...]
    notes: str | None
    notices: tuple[tuple[str, str], ...]
    section_variation_review: bool


@dataclass(frozen=True)
class IndexRow:
    group: str
    key: str
    matched: str
    srcdb: str
    code: str
    title: str


@dataclass(frozen=True)
class IndexRecord:
    term: str
    career: str
    observed_at: str
    evidence_hash: str
    announced_count: int
    search_criteria: str
    rows: tuple[IndexRow, ...]


Capture = DetailRecord | IndexRecord


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        block_tags = {"address", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2",
                      "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "section", "table",
                      "tbody", "td", "th", "tr", "ul"}
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag in block_tags:
                if parts and not parts[-1].endswith((" ", "\n", "\t")):
                    parts.append("\n")
                parts.append(child.text())
                parts.append("\n")
            else:
                parts.append(child.text())
        return "".join(parts)

    def find(self, class_name: str) -> list[_Node]:
        out = [self] if class_name in self.classes else []
        for child in self.children:
            if isinstance(child, _Node):
                out.extend(child.find(class_name))
        return out


class _Tree(HTMLParser):
    _VOID: ClassVar[set[str]] = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", [])
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _capture(value: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    try:
        kind, source_url = value["kind"], value["source_url"]
        term, career = value["term"], value["career"]
        observed_at, html = value["observed_at"], value["html"]
    except (KeyError, TypeError) as exc:
        raise ValueError("capture is missing required envelope fields") from exc
    if kind not in {"index", "detail"}:
        raise ValueError("capture kind must be index or detail")
    parsed = urlparse(source_url) if isinstance(source_url, str) else None
    if parsed is None or parsed.scheme != "https" or parsed.hostname != "atlas.emory.edu":
        raise ValueError("capture source_url must be an HTTPS Atlas URL")
    if not all(isinstance(x, str) and x.strip() for x in (term, career, observed_at, html)):
        raise ValueError("capture term, career, observed_at, and html must be non-empty strings")
    return kind, source_url, term.strip(), career.strip(), observed_at.strip(), html


def _one_text(root: _Node, class_name: str, required: bool = True) -> str | None:
    found = root.find(class_name)
    if not found:
        if required:
            raise ValueError(f"capture detail is missing .{class_name}")
        return None
    text = _plain(found[0].text())
    if required and not text:
        raise ValueError(f"capture detail has empty .{class_name}")
    return text or None


def _field(node: _Node, class_name: str) -> str | None:
    found = node.find(class_name)
    if not found:
        return None
    text = _plain(found[0].text())
    return text.split(":", 1)[-1].strip() if text else None


def read_capture(value: Mapping[str, Any]) -> Capture:
    """Parse a saved index/detail browser capture, failing closed on identity gaps."""
    kind, _url, term, career, observed_at, html = _capture(value)
    root = _Tree(html).root
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    if kind == "detail":
        code = _one_text(root, "dtl-course-code")
        title = _one_text(root, "detail-title")
        section = _one_text(root, "dtl-section", required=False)
        class_match = re.search(
            r"\bClass\s+Nbr\s*:?\s*(\S+)", section or "", re.IGNORECASE
        )
        description_section = next((n for n in root.find("section--description")
                                    if "section" in n.classes), None)
        restrictions_section = next((n for n in root.find("section--registration_restrictions")
                                     if "section" in n.classes), None)
        def content(section_node: _Node | None) -> str | None:
            if section_node is None:
                return None
            matches = section_node.find("section__content")
            return _plain(matches[0].text()) if matches else ""
        notices: list[tuple[str, str]] = []
        section_nodes: list[_Node] = []

        def collect_sections(node: _Node) -> None:
            if any(c.startswith("section--") for c in node.classes):
                section_nodes.append(node)
            for child in node.children:
                if isinstance(child, _Node):
                    collect_sections(child)

        collect_sections(root)
        for node in section_nodes:
            kind = next(c.removeprefix("section--") for c in node.classes
                        if c.startswith("section--"))
            text = content(node)
            if text:
                notices.append((kind, text))
        consent = _one_text(root, "detail-consent", required=False)
        if consent:
            notices.append(("consent", consent))
        hours = _one_text(root, "detail-hours_html", required=False)
        credits = (
            re.sub(r"^Credit\s+Hours\s*:\s*", "", hours, flags=re.IGNORECASE)
            if hours
            else None
        )
        sections: list[Section] = []
        seen_crns: set[str] = set()
        for node in root.find("course-section"):
            if node.tag != "a":
                continue
            key = node.attrs.get("data-key") or ""
            crn_match = re.fullmatch(r"crn:(.+)", key)
            crn = crn_match.group(1) if crn_match else ""
            if not crn or crn in seen_crns:
                raise ValueError("detail has missing or duplicate section CRNs")
            seen_crns.add(crn)
            sections.append(Section(crn, _field(node, "course-section-section"),
                                    _field(node, "course-section-schd"),
                                    _field(node, "course-section-camp"),
                                    _field(node, "course-section-mp"),
                                    _field(node, "course-section-instr"),
                                    _field(node, "course-section-enrl-stat-html"),
                                    key, node.attrs.get("data-group") or "",
                                    node.attrs.get("data-matched") or "",
                                    node.attrs.get("data-srcdb") or ""))
        if not code or not title:
            raise ValueError("detail identity is incomplete")
        if not sections:
            raise ValueError("detail has no valid All Sections identities")
        crns = {item.crn for item in sections}
        if class_match and class_match.group(1) not in crns:
            raise ValueError("detail class number is absent from All Sections")
        is_grouped = class_match is None
        return DetailRecord(
            code=code,
            title=title,
            description=content(description_section) or "",
            restrictions=content(restrictions_section),
            credits=credits,
            class_number=class_match.group(1) if class_match else None,
            is_grouped=is_grouped,
            career=career,
            term=term,
            observed_at=observed_at,
            evidence_hash=digest,
            sections=tuple(sections),
            notes=content(next((n for n in section_nodes if "section--classnotes" in n.classes), None)),
            notices=tuple(notices),
            section_variation_review=is_grouped,
        )

    page_text = _plain(root.text())
    criteria_nodes = (
        root.find("SearchCriteria")
        + root.find("search-criteria")
        + root.find("result-criteria__item")
    )
    criteria = _plain(" ".join(n.text() for n in criteria_nodes))
    # Atlas renders Search Criteria as a heading followed by a two-item list;
    # the heading itself has no stable class in the live markup.
    if not criteria:
        match = re.search(r"Search\s+Criteria\s*(.*)$", page_text, re.IGNORECASE)
        criteria = match.group(1) if match else ""
    if not criteria:
        raise ValueError("index capture is missing Search Criteria")
    declared_term = re.search(
        r"\b(?:Term|Semester)\s*:?\s*(.+?)(?=\s*Career\s*:|[,;]|$)",
        criteria, re.IGNORECASE,
    )
    declared_career = re.search(r"\bCareer\s*:?\s*(.+?)(?:[,;]|$)", criteria, re.IGNORECASE)
    if declared_term is None or declared_career is None:
        raise ValueError("index Search Criteria must declare term and career")
    if _plain(declared_term.group(1)).casefold() != term.casefold():
        raise ValueError("index SearchCriteria term disagrees with capture envelope")
    if _plain(declared_career.group(1)).casefold() != career.casefold():
        raise ValueError("index SearchCriteria career disagrees with capture envelope")
    count_nodes = root.find("Found") + root.find("result-count")
    count_text = _plain(" ".join(n.text() for n in count_nodes))
    if not count_text:
        count_match_text = re.search(
            r"\bFound\s*(\d+)\s*courses?\b", page_text, re.IGNORECASE
        )
        count_text = count_match_text.group(0) if count_match_text else ""
    count_match = re.search(r"\d+", count_text)
    if not count_match:
        raise ValueError("index capture is missing announced Found count")
    rows: list[IndexRow] = []
    seen_crns: set[str] = set()
    for node in root.find("result__link"):
        if node.tag != "a":
            continue
        attrs = node.attrs
        group = attrs.get("data-group") or ""
        code, title = _one_text(node, "result__code", required=False), _one_text(
            node, "result__title", required=False
        )
        if code is None and group.startswith("code:"):
            code = group[len("code:"):]
        if title is None:
            title = _plain(node.text())
        key = attrs.get("data-key")
        # Group headings are real result links too; their empty key is how Atlas
        # marks the variant parent. Keep them, then keep every child CRN row.
        if not all((group, key is not None, attrs.get("data-matched") is not None,
                    attrs.get("data-srcdb") is not None, code, title)):
            raise ValueError("index row is missing identity metadata")
        crn_match = re.fullmatch(r"crn:(.+)", key or "")
        if crn_match:
            crn = crn_match.group(1)
            if crn in seen_crns:
                raise ValueError("index capture has duplicate class CRNs")
            seen_crns.add(crn)
        rows.append(IndexRow(group, key or "", attrs.get("data-matched") or "",
                             attrs.get("data-srcdb") or "", code or "", title or ""))
    announced_count = int(count_match.group())
    if announced_count and not rows:
        raise ValueError("index announces results but has no result rows")
    return IndexRecord(term, career, observed_at, digest, int(count_match.group()), criteria,
                       tuple(rows))
