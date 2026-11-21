"""Emory facts a reply must state as the sources do: numbers, grading rules, retired and special courses."""

import re
from collections.abc import Sequence

from app.advising.research_types import (
    FinalDecision,
)
from app.atlas.designations import caveat_sentence
from app.atlas.records import normalize_code
from app.atlas.requisites import canonical_codes_in
from app.generation.answer import NONSUPPORT_RE

_RETIRED_SAID = re.compile(r"no longer (?:offered|runs?|available)|not (?:been )?offered|retired|"
                           r"discontinued|(?:isn't|aren't|not) on (?:any )?recent",
                           re.IGNORECASE)


_CLAUSE_SPLIT = re.compile(r"([;\n]|(?<=[.!?])\s+)")


_CLAUSE_END = re.compile(r"[.!?]*[\"'”’)]*$")


def disclose_retired_courses(decision: FinalDecision, retired: frozenset[str]) -> FinalDecision:
    """Say in each sentence that names a retired course that it is not offered:
    the full label at its first mention in the answer, a short one after that.

    Which courses no checked Atlas term shows is a database fact, so the server
    writes the disclosure instead of rejecting the answer for wording it
    (round 8: every model, the strongest included, lost rounds to it). The note
    goes at the end of the sentence, so it reads the same however the code was
    spelled. Recommending a retired course is still refused: that is advice,
    and a note cannot fix it.
    """
    if not retired:
        return decision
    blocks, changed, labeled = [], False, set()
    for block in decision.blocks:
        parts = _CLAUSE_SPLIT.split(block.text)
        for index in range(0, len(parts), 2):
            clause = parts[index]
            if _RETIRED_SAID.search(clause):
                continue
            named = [code for code in dict.fromkeys(canonical_codes_in(clause)) if code in retired]
            if not named:
                continue
            # The full label once per answer; a later sentence gets the short form.
            fresh = [code for code in named if code not in labeled]
            labeled.update(named)
            note = (" (not on recent schedules)" if not fresh
                    else f" ({fresh[0]} isn't on any recent Course Atlas schedule)" if len(fresh) == 1
                    else f" ({', '.join(fresh[:-1])} and {fresh[-1]} aren't on any recent "
                         "Course Atlas schedule)")
            end = _CLAUSE_END.search(clause).start()
            parts[index] = clause[:end].rstrip() + note + clause[end:]
        text = "".join(parts)
        if text != block.text:
            block, changed = block.model_copy(update={"text": text}), True
        blocks.append(block)
    return decision.model_copy(update={"blocks": tuple(blocks)}) if changed else decision


#: Special courses one answer caveats; past this the caveats would bury the answer.
MAX_SPECIAL_CAVEATS = 4


def disclose_special_courses(decision: FinalDecision, special: dict) -> FinalDecision:
    """Give each special topics, research, honors, internship, practicum or capstone
    course the answer names its caveat, once, unless the answer already says it.

    The kind is read from the Atlas title (``app.atlas.designations``), so the
    server writes the caveat rather than trusting each model to remember it. It
    goes after the first block that names the course; a block that names the
    course in the caveat's own words ("arranged with a faculty mentor") counts.
    """
    if not special:
        return decision
    blocks = list(decision.blocks)
    added = 0
    for code, designation in special.items():
        naming = [index for index, block in enumerate(blocks)
                  if block.kind != "question" and code in canonical_codes_in(block.text)]
        if not naming or any(designation.said.search(blocks[index].text) for index in naming):
            continue
        first = naming[0]
        blocks[first] = blocks[first].model_copy(update={
            "text": f"{blocks[first].text.rstrip()} {caveat_sentence(code, designation)}"})
        added += 1
        if added == MAX_SPECIAL_CAVEATS:
            break
    return decision.model_copy(update={"blocks": tuple(blocks)}) if added else decision


def retired_course_problems(decision: FinalDecision, recommended: set[str],
                            retired: frozenset[str]) -> list[str]:
    """A course the Atlas shows in no checked term is never presented as current.

    The record is deterministic and the instruction alone does not hold: a model
    that has read "no longer offered" can still recommend the course from its
    catalog description. So each sentence naming one has to say so itself.
    """
    if not retired:
        return []
    problems = [f"recommended course {code} is no longer offered; recommend the course that "
                "replaced it" for code in sorted({normalize_code(c) for c in recommended} & retired)]
    unsaid = set()
    for block in decision.blocks:
        for clause in re.split(r"[;\n]|(?<=[.!?])\s+", block.text):
            if not _RETIRED_SAID.search(clause):
                unsaid |= set(canonical_codes_in(clause)) & retired
    if unsaid:
        problems.append(
            f"{', '.join(sorted(unsaid))}: no checked Course Atlas term shows it. Say in the same "
            "sentence that it is not offered in any checked term; a student who completed it "
            "still meets a requirement that names it. Keep it in a requisite or program list, "
            "and name the current course that list gives for a student choosing now")
    return problems


# A cap, minimum or threshold: a number next to hours, credits or a GPA.
_POLICY_NUMBER = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)[\s-]*(?:semester[\s-]+|credit[\s-]+)?(?:hours?|credits?)\b"
    r"|\bGPA\b[^.\d]{0,30}?(\d+(?:\.\d{1,2})?)(?!\d)"
    r"|(?<![\d.])(\d+(?:\.\d{1,2})?)\s+(?:[a-z]+\s+)?GPA\b", re.IGNORECASE)


_WORD_NUMBER_TEXT = (
    r"(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)(?:[-\s]+(?:one|two|"
    r"three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred))*|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)")


_POLICY_WORD_NUMBER = re.compile(
    rf"\b({_WORD_NUMBER_TEXT})\s+(?=(?:credit|semester)\s+hours?\b|\bcredits?\b|"
    rf"hours?\s+of\s+(?:[a-z]+\s+){{0,2}}credits?\b)",
    re.IGNORECASE)


_NUMBER = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d])")


_UNIT_WORDS = {word: n for n, word in enumerate((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen"))}


_TEN_WORDS = {word: 10 * n for n, word in enumerate((
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), start=2)}


def _numbers_in(text: str) -> set[float]:
    """Every number a passage states, in digits or in words ("twenty-four")."""
    found = {float(number.replace(",", "")) for number in _NUMBER.findall(text)}
    total, spelled = 0, False
    for word in [*re.findall(r"[a-z]+", text.lower()), ""]:
        if word in _UNIT_WORDS or word in _TEN_WORDS:
            total += _UNIT_WORDS.get(word, 0) + _TEN_WORDS.get(word, 0)
            spelled = True
        elif word == "hundred" and spelled:
            total *= 100
        elif word == "and" and spelled:
            continue
        else:
            if spelled:
                found.add(float(total))
            total, spelled = 0, False
    return found


_GRADING_TEXT = re.compile(
    r"(?<![A-Za-z])s[/-]u(?![A-Za-z])|pass[/-]fail|credit[/-]no[- ]credit|"
    r"letter[- ]grade|satisfactory|grading basis|\bgrading\b|\bgrade mode\b",
    re.IGNORECASE)


_LETTER_ONLY_RULE = re.compile(
    r"\bno\s+s[/-]u\b|\bnot\s+(?:be\s+)?taken\s+(?:on\s+|for\s+)?s[/-]u\b|"
    r"\bletter[- ]grad(?:e|ed|ing)\b|"
    r"\bonly\b[^.;]{0,60}\belectives?\b[^.;]{0,40}(?<![A-Za-z])s[/-]u(?![A-Za-z])",
    re.IGNORECASE)


# A rule that lets electives be taken S/U ("only courses in the elective component
# may be taken S/U") makes an answer about an elective an exception, not a conflict.
_RULE_ALLOWS_ELECTIVE_SU = re.compile(
    r"\belectives?\b[^.;]{0,80}(?<![A-Za-z])s[/-]u(?![A-Za-z])|"
    r"(?<![A-Za-z])s[/-]u(?![A-Za-z])[^.;]{0,80}\belectives?\b",
    re.IGNORECASE)


_ELECTIVE_WORD = re.compile(r"\belectives?\b", re.IGNORECASE)


_SU_ALLOW = re.compile(
    r"(?<![A-Za-z])s[/-]u(?![A-Za-z])|pass[/-]fail|credit[/-]no[- ]credit|"
    r"satisfactory/?unsatisfactory", re.IGNORECASE)


_SU_PERM = re.compile(r"\b(?:can|may|allowed|eligible|permitted|option|possible)\b",
                      re.IGNORECASE)


_SU_NEG = re.compile(
    r"\b(?:no|not|n['’]t|never|cannot|can['’]t|prohibit(?:ed)?|ineligible|"
    r"disallowed|forbidden|must\s+be\s+(?:taken\s+(?:for\s+)?)?a?\s*letter)\b",
    re.IGNORECASE)


_ELECTIVE_EXCEPTION = re.compile(
    r"\b(?:general\s+electives?|free\s+electives?|non-major|outside\s+the\s+(?:major|minor))\b",
    re.IGNORECASE)


def grading_rule_problems(decision: FinalDecision,
                          rules: Sequence[tuple[str, str, str]]) -> list[str]:
    """A grading answer must cite and follow the program's own rule when one was read."""
    if not rules:
        return []
    written = "\n".join(block.text for block in decision.blocks)
    if not _GRADING_TEXT.search(written):
        return []
    cited = {eid for block in decision.blocks for eid in block.evidence_ids}
    cited_rules = [(title, text, evidence_id) for title, text, evidence_id in rules
                   if evidence_id in cited]
    if not cited_rules:
        title, text, _evidence_id = rules[0]
        return [(f"the answer discusses grading but does not cite {title}'s own rule "
                 f"({text[:300]}); use that rule instead of the College default")]
    for title, text, _eid in cited_rules:
        if not _LETTER_ONLY_RULE.search(text):
            continue
        electives_allowed = bool(_RULE_ALLOWS_ELECTIVE_SU.search(text))
        for clause in re.split(r"[;\n]|(?<=[.!?])\s+", written):
            if (_SU_ALLOW.search(clause) and _SU_PERM.search(clause)
                    and not _SU_NEG.search(clause)
                    and not _ELECTIVE_EXCEPTION.search(clause)
                    and not (electives_allowed and _ELECTIVE_WORD.search(clause))):
                return [(f"the answer states courses may be taken S/U in conflict with "
                         f"{title}'s grading rule ({text[:300]})")]
    return []


def policy_number_problems(decision: FinalDecision, records: dict) -> list[str]:
    """A fact or judgment stating a cap, minimum or GPA threshold cites a passage with it.

    A model that read the College default and the program rule can give the
    wrong one's number, or none of them ("24 hours" when the rule says 12). Advice
    carries numbers as often as facts do; only its opinion is beyond checking.
    """
    problems = []
    for block in decision.blocks:
        if block.kind not in {"fact", "judgment"}:
            continue
        support = _numbers_in("\n".join(
            records[e].exact_text for e in block.evidence_ids if e in records))
        unsupported = set()
        for clause in re.split(r"[;\n]|(?<=[.!?])\s+", block.text):
            if NONSUPPORT_RE.search(clause):
                continue
            for match in _POLICY_NUMBER.finditer(clause):
                number = next(group for group in match.groups() if group)
                if float(number) not in support:
                    unsupported.add(match.group(0).strip())
            # "twenty-four credit hours": the number itself can be written out.
            for match in _POLICY_WORD_NUMBER.finditer(clause):
                for number in _numbers_in(match.group(1)):
                    if number not in support:
                        unsupported.add(f"{number:g}")
        if unsupported:
            problems.append(
                f"{block.kind} block states " + ", ".join(sorted(unsupported)) + " but its cited passages "
                "do not contain that number; cite the passage that states it, or drop it")
    return problems
