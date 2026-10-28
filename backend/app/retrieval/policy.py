"""Deterministic detection of policy obligations mixed into a program query.

This is a retrieval-routing hint, not a classifier of user intent.  It only
prevents named-program evidence from consuming every citation slot when the
question also asks about an academic rule.  Program-only questions keep the
existing exact-program allocation.
"""

from __future__ import annotations

import re

_POLICY_TERMS = re.compile(
    r"\b(?:"
    r"s\s*/\s*u|satisfactory\s*/?\s*unsatisfactory|"
    r"credit\s+hours?|semester\s+hours?|"
    r"gpa|grade\s+point\s+average|"
    r"deadline|repeat|retak(?:e|ing)|withdraw(?:al|n)?|"
    r"drop(?:ping)?|overload|"
    r"add(?:ing)?\s+(?:a\s+)?minor|change\s+(?:of\s+)?major|"
    r"double[\s-]?count|double\s+major|two\s+majors|"
    r"major\s+and\s+(?:a\s+)?minor|"
    r"advanced\s+placement|ap\s+credit|transfer\s+credit|"
    r"graduat(?:e|ion)|academic\s+standing|probation|incomplete|"
    r"readmission|full[\s-]?time|part[\s-]?time"
    r")\b",
    re.IGNORECASE,
)


def has_policy_terms(query: str) -> bool:
    """True when the query states an academic-rule obligation or deadline."""
    return bool(_POLICY_TERMS.search(query or ""))


def program_reserve_cap(query: str, cited_set_size: int) -> int | None:
    """Maximum program-evidence slots for a mixed query, or None if program-only.

    Leave two slots for policy/rule evidence.  A small citation budget still
    yields a non-negative allocation; the ordinary diversity allocator fills
    any remaining slots.
    """
    if not has_policy_terms(query):
        return None
    return max(0, cited_set_size - 2)
