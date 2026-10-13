"""Department program-requirement pages -> Markdown. No network.

Emory's catalog carries no program requirements, so they come from the
department sites (Emory's shared web template). Only the page's content
column is kept; navigation, sidebars, and footers are dropped. Tables become
Markdown tables so "one course chosen from: ..." rows stay readable.
"""

import re
from html.parser import HTMLParser

_VOID = {"br", "hr", "img", "input", "meta", "link", "wbr", "source", "col"}
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript",
              "svg", "button"}
_SKIP_ATTR_RE = re.compile(r"sidebar|breadcrumb|skip-link|visually-hidden|sr-only",
                           re.IGNORECASE)
_REGION_IDS = ("main-column", "main-content", "main")
_BLOCK = {"p", "div", "section", "article", "address", "blockquote", "caption", "dl", "dt", "dd"}
_HEADINGS = {f"h{n}": n for n in range(1, 7)}


class _Converter(HTMLParser):
    def __init__(self, region_id: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.region_id = region_id
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, opens_region, skipped)
        self.in_region = region_id is None
        self.skip = 0
        self.out: list[str] = []
        self.lists: list[str] = []
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None

    # -- output helpers
    def _emit(self, text: str) -> None:
        if not self.in_region or self.skip:
            return
        if self.cell is not None:
            self.cell.append(text)
        else:
            self.out.append(text)

    def _block(self, sep: str = "\n\n") -> None:
        self._emit(" " if self.cell is not None else sep)

    # -- parser hooks
    def handle_starttag(self, tag, attrs) -> None:
        attrs = dict(attrs)
        opens = (not self.in_region and self.region_id is not None
                 and attrs.get("id") == self.region_id)
        skipped = tag in _SKIP_TAGS or bool(
            _SKIP_ATTR_RE.search(f"{attrs.get('id', '')} {attrs.get('class', '')}")
        )
        if tag not in _VOID:
            self.stack.append((tag, opens, skipped))
        if opens:
            self.in_region = True
        if skipped:
            self.skip += 1
            if tag in _VOID:
                self.skip -= 1
            return
        if tag in _HEADINGS:
            self._block()
            if self.cell is None:
                self._emit("#" * _HEADINGS[tag] + " ")
        elif tag in _BLOCK:
            self._block()
        elif tag == "br":
            self._block("\n")
        elif tag == "hr":
            self._block()
        elif tag in ("ul", "ol"):
            self.lists.append(tag)
            self._block("\n")
        elif tag == "li":
            indent = "  " * max(len(self.lists) - 1, 0)
            self._block("\n")
            if self.cell is None:
                self._emit(f"{indent}- ")
        elif tag == "table" and self.in_region and not self.skip:
            self._flush_table()
            self.out.append("\n\n")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.table.append([])
        elif tag in ("td", "th") and self.table is not None:
            self.cell = []

    def handle_endtag(self, tag) -> None:
        if tag in _VOID or not any(t == tag for t, _, _ in self.stack):
            return
        while self.stack:
            open_tag, opens, skipped = self.stack.pop()
            if skipped:
                self.skip -= 1
            self._close(open_tag)
            if opens:
                self.in_region = False
            if open_tag == tag:
                break

    def _close(self, tag: str) -> None:
        if self.skip:
            return
        if tag in _HEADINGS or tag in _BLOCK:
            self._block()
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            self._block("\n")
        elif tag in ("td", "th") and self.cell is not None and self.table is not None:
            text = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "/")
            if self.table:
                self.table[-1].append(text)
            self.cell = None
        elif tag == "table":
            self._flush_table()

    def _flush_table(self) -> None:
        if self.table is None:
            return
        rows = [row for row in self.table if any(cell for cell in row)]
        self.table = None
        self.cell = None
        if not rows:
            return
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
        self.out.append("\n\n" + "\n".join(lines) + "\n\n")

    def handle_data(self, data) -> None:
        self._emit(data)


def _tidy(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t ]+", " ", line).rstrip()
        if line.strip() in ("-", "#", "##", "###", "####", "#####", "######"):
            continue
        lines.append(line if line.startswith(" ") and line.lstrip().startswith("- ")
                     else line.strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def page_to_markdown(page: str) -> str:
    """The content column of a department page as Markdown."""
    for region_id in (*_REGION_IDS, None):
        if region_id is not None and f'id="{region_id}"' not in page:
            continue
        converter = _Converter(region_id)
        converter.feed(page)
        converter.close()
        converter._flush_table()
        text = _tidy("".join(converter.out))
        if text.strip():
            return text
    return ""
