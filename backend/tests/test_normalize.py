"""Normalization: same bytes, same string, every time."""

from app.ingest.normalize import decode_text, normalize_text


def test_bom_is_stripped():
    assert decode_text(b"\xef\xbb\xbfhello") == "hello"


def test_cp1252_fallback():
    assert decode_text(b"caf\xe9") == "café"


def test_crlf_and_cr_become_lf():
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"


def test_nul_and_c0_controls_removed_but_tab_and_newline_kept():
    assert normalize_text("a\x00b\x07c\td\ne") == "abc\td\ne"


def test_idempotent():
    once = normalize_text("a\r\nb\x00")
    assert normalize_text(once) == once


def test_soft_hyphen_removed():
    assert normalize_text("co\u00adoperate") == "cooperate"


def test_zero_width_and_bidi_controls_removed():
    assert normalize_text("a\u200bb\u200dc\u2060d\ufeffe") == "abcde"
    assert normalize_text("x\u202ey") == "xy"


def test_c1_controls_removed():
    assert normalize_text("a\x85b\x9fc") == "abc"


def test_utf16_bom_decoded():
    assert decode_text("hello".encode("utf-16")) == "hello"
    assert decode_text("hello".encode("utf-32")) == "hello"


def test_utf8_bom_still_stripped():
    assert decode_text(b"\xef\xbb\xbfhi") == "hi"
