"""Normalization: same bytes, same string, every time."""

import pytest

from app.ingest.normalize import decode_text, normalize_text


@pytest.mark.parametrize(("raw", "expected"), [
    (b"\xef\xbb\xbfhello", "hello"),  # UTF-8 BOM
    (b"caf\xe9", "caf\u00e9"),  # cp1252 fallback
    ("hello".encode("utf-16"), "hello"),
    ("hello".encode("utf-32"), "hello"),
])
def test_decode_text(raw, expected):
    assert decode_text(raw) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("a\r\nb\rc", "a\nb\nc"),  # CRLF and CR become LF
    ("a\x00b\x07c\td\ne", "abc\td\ne"),  # C0 controls go; tab and newline stay
    ("a\x85b\x9fc", "abc"),  # C1 controls
    ("co\u00adoperate", "cooperate"),  # soft hyphen
    ("a\u200bb\u200dc\u2060d\ufeffe", "abcde"),  # zero-width characters
    ("x\u202ey", "xy"),  # bidi control
])
def test_normalize_text(text, expected):
    assert normalize_text(text) == expected
    assert normalize_text(expected) == expected  # idempotent
