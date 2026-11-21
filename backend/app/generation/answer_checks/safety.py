"""What no reply may contain: stubs, echoed instructions, secrets, markup, unknown links, credential asks."""

import re
import unicodedata

# What an answer is made of, checked before what it claims. A model that is
# injected or misfires can return a stub, its own instructions, or text shaped
# like a secret; none of it may reach a student.
_STUB = re.compile(r"(?:placeholder|lorem ipsum|todo|tbd|n/?a|answer|text|none|null)?",
                   re.IGNORECASE)


_STUB_START = re.compile(r"^(?:placeholder|lorem ipsum|todo|tbd)\b", re.IGNORECASE)


_STUB_INLINE = re.compile(
    r"\[\s*(?:todo|tbd|placeholder|insert\b[^\]]{0,40})\s*\]|\b(?:lorem ipsum|todo\s*:|tbd\s*:)",
    re.IGNORECASE)


_MIN_ANSWER_LETTERS = 12


_ECHO_SHINGLE = 5


_ECHO_MIN_SHARED = 5


_ECHO_RATIO = 0.25


_ZERO_WIDTH = str.maketrans("", "", "\u00ad\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\ufeff")


_CONFUSABLES = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "І": "I", "Ј": "J", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S",
    "а": "a", "е": "e", "і": "i", "ј": "j", "к": "k", "м": "m", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
    "τ": "t", "υ": "u", "χ": "x",
})


_SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}|\b[A-Z][A-Z0-9_]{2,}_(?:API_)?KEY\s*=|"
    r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@|-----BEGIN [A-Z0-9 ]*KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"\bgh[pousr]_[A-Za-z0-9]{36}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}|"
    r"\bAIza[0-9A-Za-z_-]{35}\b|"
    r"(?i:\bBearer\s+[A-Za-z0-9._-]{16,}|\bAccountKey\s*=\s*[^\s;]{8,}|"
    r"\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|pwd)\s*[:=]\s*"
    r"(?=['\"]?(?:[^\s'\";]*[\d_/+=-]|[A-Za-z]{16,}))['\"]?[A-Za-z0-9_/+=-]{8,})|"
    r"Traceback \(most recent call last\):|File \"[^\"]+\", line \d+")


_MARKUP = re.compile(
    r"<\s*(?:script|style|iframe|object|embed|svg)\b|javascript:|data\s*:\s*text/html|"
    r"\bon(?:click|error|load|mouseover|mouseout|focus|blur|submit|change|input|keydown|keyup)\s*=",
    re.IGNORECASE)


_URL = re.compile(
    r"\bhttps?://[^\s)\]>\"']+|\bwww\.[^\s)\]>\"']+|"
    r"(?<=\]\()(?:[a-z0-9-]{2,}\.)+[a-z]{2,10}(?:/[^\s)]*)?(?=\))|"
    r"(?<![A-Za-z0-9.@])(?:[a-z0-9-]{2,}\.)+"
    r"(?:com|org|net|example|xyz|info|biz|[a-z]{2,10}(?=/[^\s)\]>\"']+))(?:/[^\s)\]>\"']*)?",
    re.IGNORECASE)


_CREDENTIAL_ASK = re.compile(
    r"\b(?:verify|enter|provide|send|confirm|share|submit|type|give|reply\s+with|tell\s+me)\b"
    r"[^.!?\n]{0,60}\b(?:password|passcode|credentials|ssn|social\s+security(?:\s+number)?|"
    r"api[\s_-]?key|secret[\s_-]?token|access[\s_-]?token)\b"
    r"|\b(?:send|share|give|reply\s+with|tell\s+me)\b[^.!?\n]{0,40}\b(?:your\s+)?(?:netid|login|pin)\b",
    re.IGNORECASE)


_SPACED_OBF = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9](?:\s+[A-Za-z0-9]){2,}(?![A-Za-z0-9])")


_DOTTED_OBF = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9](?:\.[A-Za-z0-9]){3,}(?![A-Za-z0-9])")


_DOT_SPACED = re.compile(
    r"(?<=[A-Za-z0-9])\s*\.\s*(?=(?:com|org|net|edu|gov|io|co|example|xyz|info|biz)\b)|"
    r"(?<=[A-Za-z0-9])\s+\.\s+(?=[A-Za-z0-9])|(?<=\bwww)\s*\.\s*(?=[A-Za-z0-9])",
    re.IGNORECASE,
)


_SCHEME_SPACED = re.compile(r"\b(https?)\s*:\s*/\s*/", re.IGNORECASE)


def _clean_safety_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    without_marks = "".join(
        ch for ch in unicodedata.normalize("NFKD", normalized)
        if unicodedata.category(ch) != "Mn"
    )
    return without_marks.translate(_ZERO_WIDTH).translate(_CONFUSABLES)


def _safety_variants(text: str) -> tuple[str, ...]:
    clean = _clean_safety_text(text)
    spaced = _SPACED_OBF.sub(lambda m: re.sub(r"\s+", "", m.group(0)), clean)
    spaced = _SCHEME_SPACED.sub(r"\1://", _DOT_SPACED.sub(".", spaced))
    dotted = _DOTTED_OBF.sub(lambda m: m.group(0).replace(".", ""), clean)
    return tuple(dict.fromkeys((text or "", clean, spaced, dotted)))


def _normalize_match_text(text: str) -> str:
    return _clean_safety_text(text).casefold()


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", _normalize_match_text(text))
    return {tuple(words[i:i + _ECHO_SHINGLE]) for i in range(len(words) - _ECHO_SHINGLE + 1)}


def _is_stub_block(text: str) -> bool:
    for variant in _safety_variants(text):
        if (not re.search(r"[A-Za-z]", variant)
                or _STUB.fullmatch(variant.strip(" .,:;!?-*#\"'[]"))
                or _STUB_START.match(variant.strip())
                or _STUB_INLINE.search(variant)):
            return True
    return False


def _asks_for_credentials(text: str) -> bool:
    for match in _CREDENTIAL_ASK.finditer(text):
        prefix = text[max(0, match.start() - 24):match.start()].lower()
        if re.search(r"\b(?:never|not|don['’]t|avoid|no need to)\b", prefix):
            continue  # safety advice, not a request
        return True
    return False


_INSTRUCTION_SHINGLES: dict[int, set[tuple[str, ...]]] = {}


def _instruction_shingles(instructions: str) -> set[tuple[str, ...]]:
    # The instructions are a few fixed strings; shingle each once.
    key = hash(instructions)
    if key not in _INSTRUCTION_SHINGLES:
        _INSTRUCTION_SHINGLES[key] = _shingles(instructions)
    return _INSTRUCTION_SHINGLES[key]


def answer_safety_problems(decision, *, instructions: str | None, evidence) -> list[str]:
    """Reject a reply that is empty, echoes the instructions, or carries a secret or link."""
    texts = [block.text for block in decision.blocks]
    problems = []
    if any(_is_stub_block(text) for text in texts) or sum(
            len(re.findall(r"[A-Za-z]", _clean_safety_text(t))) for t in texts) < (
                _MIN_ANSWER_LETTERS):
        problems.append("a block has no answer in it; write the answer itself in plain sentences")
    written = "\n".join(texts)
    variants = _safety_variants(written)
    reply_shingles = _shingles(written)
    prompt_shingles = _instruction_shingles(instructions) if instructions else set()
    shared = len(reply_shingles & prompt_shingles)
    if (shared >= _ECHO_MIN_SHARED
            and shared / max(1, len(reply_shingles)) >= _ECHO_RATIO):
        problems.append("the reply repeats the advisor's instructions; answer the student instead")
    if any(_SECRET.search(variant) for variant in variants):
        problems.append("the reply contains credential, key or error text; remove it")
    if any(_MARKUP.search(variant) for variant in variants):
        problems.append("the reply contains HTML markup; answer in plain text")
    known = "\n".join(
        f"{record.source_name}\n{record.source_url or ''}\n{record.exact_text}"
        for record in evidence).lower()
    if any(re.sub(r"^https?://", "", url.rstrip(".,;:!?").lower()).rstrip("/") not in known
           for variant in variants for url in _URL.findall(variant)):
        problems.append("the reply links to a page no evidence names; remove the link")
    if any(_asks_for_credentials(variant) for variant in variants):
        problems.append("the reply asks the student for a password or credentials; remove it")
    return problems
