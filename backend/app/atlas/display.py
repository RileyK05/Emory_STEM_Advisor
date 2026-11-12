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


def canonicalize_codes(text: str) -> str:
    """Rewrite a written course code to the Atlas identity it resolves to."""
    if not text:
        return text

    def replace(match: re.Match) -> str:
        canonical = canonical_codes_in(match.group(0))
        return canonical[0] if len(canonical) == 1 else match.group(0)

    return _WRITTEN_CODE.sub(replace, text)
