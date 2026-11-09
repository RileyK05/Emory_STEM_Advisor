"""Answer value types shared by research rendering, evidence and serialization."""

import re
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Citation:
    source_name: str
    locator_label: str
    chunk_id: UUID
    source_id: UUID


@dataclass(frozen=True)
class GeneratedAnswer:
    text: str
    citations: tuple[Citation, ...]
    system_prompt: str
    user_prompt: str
    prompt_tokens: int | None
    completion_tokens: int | None
    warnings: tuple[str, ...]
    usage_source: str = "unknown"
    model_id: str | None = None
    cached_prompt_tokens: int | None = None


# Every branch here is a statement that a course is absent from a source or a
# requirement list. A clause that denies presence may name the absent course;
# a clause that asserts a requirement may not. "does not require" is
# deliberately absent: it is a claim about a requirement, not about presence.
NONSUPPORT_RE = re.compile(
    r"\b(?:does not support|do not support|not support|no evidence|"
    r"not in the|cannot determine|not established|not found|"
    r"does not (?:show|state|describe|list|include|contain|cover|mention|name|"
    r"appear|occur)|"
    r"do not (?:show|state|describe|list|include|contain|cover|mention|name|"
    r"appear|occur)|"
    r"cannot (?:confirm|verify)|contains no|names no|"
    r"no supplied (?:passage|source)(?:\s+or\s+[a-z][a-z ]{0,60})?\s+"
    r"(?:establishes|names|contains|lists)|"
    r"does not (?:establish|substitute|verify|confirm|validate)|"
    r"cannot (?:establish|substitute|verify|confirm|validate)|"
    r"no basis|gives no basis|neither (?:course )?code (?:appears|is named)|"
    r"not (?:listed|included|contained|named|part of|among)|unresolved here|"
    r"no course (?:numbered|named|coded|listed)|there (?:is|are) no|"
    r"(?:is|are) (?:absent|missing)|"
    r"(?:was|were)\s+not\s+(?:captured|retained|available|consulted)\b|"
    r"no\b[^.]{0,80}\b(?:was|were)\s+(?:read|captured|consulted|found)\b|"
    r"neither\b[^.]{0,120}\b(?:appears?|occurs?)\b)\b",
    re.IGNORECASE,
)
