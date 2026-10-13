"""Build a small multi-page PDF with an extractable text layer, no dependencies."""


def _escape_pdf_string(text: str) -> str:
    """Escape the delimiters of a PDF literal string."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_content(text: str) -> bytes:
    return f"BT /F1 24 Tf 72 700 Td ({_escape_pdf_string(text)}) Tj ET".encode("latin-1")


def make_pdf(page_texts: list[str], *, raw_content: dict[int, bytes] | None = None) -> bytes:
    """Build a PDF. `raw_content` overrides a page's content stream by index."""
    n = len(page_texts)
    font_obj = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode("latin-1"),
    ]
    overrides = raw_content or {}
    for i, text in enumerate(page_texts):
        content = overrides.get(i, _text_content(text))
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {4 + 2 * i} 0 R "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> >>"
            ).encode("latin-1")
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode("latin-1")
            + content
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)
