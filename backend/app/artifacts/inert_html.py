"""Render untrusted HTML as allowlisted inert markup, never executable source."""
import base64
import binascii
import html
import re
from html.parser import HTMLParser

# Static SVG shapes and text, as the diagram and chart renderers draw them. No
# element that loads, links, animates or embeds is kept (use, image, a,
# foreignObject, animate, set), and no attribute that holds a URL.
_SVG = frozenset({
    "svg", "g", "defs", "marker", "rect", "path", "line", "polyline", "polygon",
    "circle", "ellipse", "text", "tspan",
})
_ALLOWED = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "ul", "ol", "li", "dl", "dt", "dd", "div", "span", "b", "strong",
    "em", "i", "u", "small", "mark", "sub", "sup", "br", "hr",
    "details", "summary", "figure", "figcaption", "img", "section", "header", "article",
    "title", *_SVG,  # title: see below
})
_DROP_CONTENT = frozenset({
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "portal", "template", "noscript", "math", "foreignobject",
})
_VOID = frozenset({"br", "hr", "img"})
_ATTRIBUTES = frozenset({
    "id", "class", "title", "lang", "dir", "role", "style", "colspan", "rowspan",
    "scope", "start", "reversed", "open", "alt", "width", "height",
})
_SVG_ATTRIBUTES = frozenset({
    "xmlns", "viewbox", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "d", "points", "fill", "stroke", "stroke-width", "stroke-dasharray", "opacity",
    "font-size", "font-weight", "font-family", "text-anchor", "transform",
    "refx", "refy", "markerwidth", "markerheight", "orient", "marker-end",
})
# A reference may name a marker in the same drawing and nothing else.
_LOCAL_REFERENCE = re.compile(r"url\(#[A-Za-z0-9_-]+\)")
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _svg_value(key, value):
    lowered = value.casefold()
    if key == "xmlns":
        return value if value == _SVG_NAMESPACE else None
    if "url(" in lowered:
        return value if _LOCAL_REFERENCE.fullmatch(value.strip()) else None
    if "javascript:" in lowered or "expression(" in lowered:
        return None
    return value


def _image(value):
    for prefix in ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,"):
        if value.startswith(prefix):
            try:
                decoded = base64.b64decode(value[len(prefix):], validate=True)
            except (binascii.Error, ValueError):
                return None
            return value if 0 < len(decoded) <= 1_000_000 else None
    return None


class _InertParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.suppressed = []
        self.open_tags = []
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 20_000:
            raise ValueError("HTML preview has too many elements")
        if self.suppressed:
            if tag in _DROP_CONTENT and tag not in {"embed", "frame"}:
                self.suppressed.append(tag)
            return
        if tag in _DROP_CONTENT:
            if tag not in {"embed", "frame"}:
                self.suppressed.append(tag)
            return
        if tag not in _ALLOWED or (tag == "title" and "svg" not in self.open_tags):
            # A page-level <title> would hide its text; inside a drawing it is the
            # accessible name.
            return
        clean = []
        seen = set()
        for key, value in attrs:
            if key in seen or value is None:
                continue
            seen.add(key)
            if key in _ATTRIBUTES or key.startswith("aria-"):
                clean.append((key, value))
            elif tag in _SVG and key in _SVG_ATTRIBUTES:
                safe = _svg_value(key, value)
                if safe is not None:
                    clean.append((key, safe))
            elif tag == "img" and key == "src":
                safe = _image(value)
                if safe:
                    clean.append((key, safe))
        # No URLs, event handlers, forms, refresh/base tags, active namespaces,
        # or untrusted markup are copied into the trusted output structure.
        self.output.append("<" + tag + "".join(
            " " + key + '="' + html.escape(value, quote=True) + '"' for key, value in clean) + ">")
        if tag not in _VOID:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag in self.open_tags:
            while self.open_tags:
                last = self.open_tags.pop()
                self.output.append("</" + last + ">")
                if last == tag:
                    break

    def handle_data(self, data):
        if not self.suppressed:
            self.output.append(html.escape(data, quote=False))

    def finish(self):
        self.close()
        while self.open_tags:
            self.output.append("</" + self.open_tags.pop() + ">")
        return "".join(self.output)


def render_inert_markup(source):
    parser = _InertParser()
    parser.feed(source)
    return parser.finish()
