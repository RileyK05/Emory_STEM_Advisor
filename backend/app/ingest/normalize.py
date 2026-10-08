"""Text normalization. The same input bytes always produce the same string."""

import codecs
import re
import unicodedata

# C0 controls except tab (0x09) and newline (0x0A), plus the C1 block
# (0x80-0x9f), which cp1252-decoded text can leave behind. NUL must go:
# Postgres text rejects it.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Zero-width and bidi-control characters. Invisible at extraction, but they
# corrupt tokenization (words split, embeddings polluted) and are never
# meaningful in our corpus.
_INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060\ufeff]"
)
# Soft hyphen: a discretionary hyphen from wrapped source, not a real character.
_SOFT_HYPHEN = "\u00ad"

# Byte-order marks, longest first so UTF-32 is not mistaken for UTF-16.
_BOMS = (
    ("utf-32", codecs.BOM_UTF32_LE),
    ("utf-32", codecs.BOM_UTF32_BE),
    ("utf-16", codecs.BOM_UTF16_LE),
    ("utf-16", codecs.BOM_UTF16_BE),
    ("utf-8-sig", codecs.BOM_UTF8),
)


def decode_text(raw: bytes) -> str:
    """Decode bytes, honoring a byte-order mark, then UTF-8, then cp1252.

    A UTF-16/UTF-32 file without a BOM is not detected (it decodes to bytes
    UTF-8 can't read and falls to cp1252); a BOM is the only reliable signal,
    and one that is present must not be silently mangled.
    """
    for encoding, bom in _BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                break
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def normalize_text(text: str) -> str:
    """Normalize newlines and Unicode, strip control and invisible characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_SOFT_HYPHEN, "")
    text = _INVISIBLE_RE.sub("", text)
    return _CONTROL_RE.sub("", text)
