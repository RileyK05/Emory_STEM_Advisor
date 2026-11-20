"""Student-facing spelling of course identities.

The parser normalizes old and alternate subject spellings for identity
(BIO -> BIOL, QTM -> DATASCI, PSYCH -> PSYC). An artifact must print the code
the Course Atlas has, not the alias the model happened to write.
"""

from __future__ import annotations

import re

from app.atlas.requisites import canonical_codes_in

_WRITTEN_CODE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]{2,8}(?:_OX)?\s?\d{3}[A-Za-z]?(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def canonical_code(code: str) -> str:
    """The Atlas identity of one written code, when it resolves to exactly one."""
    codes = canonical_codes_in(code)
    return codes[0] if len(codes) == 1 else code


def canonicalize_codes(text: str) -> str:
    """Rewrite each written course code in a text to the Atlas identity it resolves to."""
    if not text:
        return text
    return _WRITTEN_CODE.sub(lambda match: canonical_code(match.group(0)), text)
