"""Course codes a reply names: that they exist and that the cited evidence backs them."""

import re

from app.atlas.records import normalize_code
from app.atlas.requisites import canonical_codes_in
from app.generation.answer import NONSUPPORT_RE

# A fact block that asserts a course is required, listed or part of a program
# must be backed by the passage it cites. A block that only describes a course
# makes no such assertion.
_INCLUSION = re.compile(
    r"\b(?:required|requirement|elective|counts?|sits? within|list(?:ed)?|"
    r"program|major|minor|degree)\b", re.IGNORECASE)


_ENTRY = re.compile(
    r"\b(?:entry point|starting point|first course|initial course|"
    r"begin with|start with)\b", re.IGNORECASE)


_ENTRY_NEGATED = re.compile(
    r"\b(?:not|never|rather than|instead of)\b[^.]{0,80}"
    r"\b(?:entry point|starting point|first course|initial course)\b", re.IGNORECASE)


_PREREQUISITE = re.compile(r"\b(?:prerequisite|prerequisites|requisite|requisites)\b",
                           re.IGNORECASE)


def _program_requirement_mentions(records, cited: set[str]) -> set[str]:
    """Codes mentioned in cited program reads, including alternatives and exclusions.

    A mention alone does not prove eligibility or support a course recommendation.
    Program evidence carries the issued program handle as an entity reference;
    the parser handles catalog shorthand such as ``BIOL 141/141L``.
    """
    mentioned: set[str] = set()
    for evidence_id in cited:
        record = records.get(evidence_id)
        if record is None or not any(ref.startswith("h_") for ref in record.entity_refs):
            continue
        mentioned.update(canonical_codes_in(record.exact_text))
    return mentioned


# A clause that names a course the master says does not exist must say so. The
# finish gate requires naming it, so this is the one wording that may.
_ABSENT_SAID = re.compile(
    r"\b(?:does(?: not|n['’]t) exist|no such (?:course|class)|"
    r"(?:is|are)(?: not|n['’]t) (?:a |an )?(?:current |real |valid )?(?:emory )?"
    r"(?:course|class)(?:es)?\b|"
    r"there (?:is|are) no (?:[A-Z][A-Z0-9]{1,7}(?:_OX)?\s?\d{3}[A-Z]?|"
    r"such (?:course|class)|course|class)\b|"
    r"(?:not|(?:is|are)n['’]t) (?:in|found in|listed in) the course atlas|no record of)",
    re.IGNORECASE)


_CLAUSE_BREAK = r"[;\n]|(?<=[.!?])\s+"


def established_codes(records, known_course_codes) -> set[str]:
    """Every course code this run established: in a passage it read, or in the master.

    Identity and claim support are two different questions, checked at two levels.
    Identity: a course the answer names must exist. The course master is the
    authority on existence, and the run's evidence on what the run established: a
    course the run never read is still a course, and one in no master and no
    passage is not a course at all. That is about the run and the collection, not
    one block's citations. Claim support (cited_support_problems) asks whether the
    passage a block cites establishes what it asserts.
    """
    established: set[str] = set()
    for record in records.values():
        established |= set(canonical_codes_in(
            f"{record.locator_label}\n{record.exact_text}"))
    established |= _program_requirement_mentions(records, set(records))
    if known_course_codes:
        established |= set(known_course_codes)
    return established


def absent_code_problems(decision, absent_codes) -> list[str]:
    """A code the student asked about that is no course, named without saying so.

    Naming one is how an answer says it does not exist, so a clause that says so is
    not an identity claim; a clause that names one any other way treats it as a course.
    """
    unsaid_absent: set[str] = set()
    for block in decision.blocks:
        for clause in re.split(_CLAUSE_BREAK, block.text):
            if not _ABSENT_SAID.search(clause):
                unsaid_absent |= set(canonical_codes_in(clause)) & absent_codes
    if unsaid_absent:
        return [(f"{', '.join(sorted(unsaid_absent))} is not a course; say in the same sentence "
                 "that it does not exist, and claim nothing else about it")]
    return []


def identity_problems(decision, established) -> list[str]:
    """A fact or limitation that names a course this run never established."""
    problems = []
    for block in decision.blocks:
        if block.kind not in {"fact", "limitation"}:
            # A judgment compares candidates the run already read; a question
            # asks the student something. Neither asserts an institutional
            # fact about a course, so neither is an identity claim.
            continue
        missing = set()
        for clause in re.split(_CLAUSE_BREAK, block.text):
            named = set(canonical_codes_in(clause)) - established
            if not named or NONSUPPORT_RE.search(clause):
                # A clause that says the evidence does not support the claim
                # may name the absent code; that is the point of the clause.
                continue
            missing.update(named)
        if missing:
            problems.append(
                f"{block.kind} block names course code(s) absent from the evidence "
                "this run acquired: " + ", ".join(sorted(missing)))
    return problems


def cited_support_problems(decision, records, absent_codes) -> list[str]:
    """A block that says a course counts toward a program, naming one its citations lack."""
    problems = []
    for block in decision.blocks:
        # Advice that says a course counts toward a program makes the same
        # claim a fact does, so it names only courses its citations show.
        if block.kind not in {"fact", "judgment"} or not _INCLUSION.search(block.text):
            continue
        # The locator is part of the cited passage: a course record's locator
        # names the course the passage is about.
        support = "\n".join(
            f"{records[e].locator_label}\n{records[e].exact_text}"
            for e in block.evidence_ids if e in records)
        present = set(canonical_codes_in(support)) | _program_requirement_mentions(
            records, set(block.evidence_ids)) | absent_codes
        missing = set()
        for clause in re.split(_CLAUSE_BREAK, block.text):
            named = set(canonical_codes_in(clause)) - present
            if not named or NONSUPPORT_RE.search(clause):
                continue
            missing.update(named)
        if missing:
            problems.append(
                f"{block.kind} block names course code(s) absent from its cited evidence: "
                + ", ".join(sorted(missing)))
    return problems


def entry_claim_problems(decision, records) -> list[str]:
    """A "start with" claim must cite the course's record, and state its prerequisite first."""
    problems = []
    for block in decision.blocks:
        if (block.kind not in {"fact", "judgment"} or not _ENTRY.search(block.text)
                or _ENTRY_NEGATED.search(block.text)):
            continue
        mentions = canonical_codes_in(block.text)
        if not mentions:
            continue
        course = mentions[0]
        support = [records[e] for e in block.evidence_ids
                   if e in records and course in set(records[e].entity_refs)]
        if not support:
            problems.append(
                f"entry/starting-point claim names {course} without citing its course record")
        elif any(_PREREQUISITE.search(record.exact_text) for record in support):
            prerequisite_codes = {
                code for record in support for code in canonical_codes_in(record.exact_text)
            } - {course}
            if not prerequisite_codes & set(mentions):
                problems.append(
                    f"entry/starting-point claim for {course} cites a record listing a prerequisite; "
                    "state the prerequisite first")
    return problems


def advised_courses(decision, records) -> set[str]:
    """The courses a reply advises: the ones it recommends and the ones its judgments name.

    A judgment that names a course is advising the student to consider it, so the
    same read/current/exists checks apply however the model typed it. A course
    that a cited course record names besides its own (a prerequisite, say) is
    mentioned, not advised, and that read grounds it. The record's own course stays
    a recommendation, so the retired check still sees it. A program list's mention
    grounds nothing: citing the list would otherwise ground advice about every
    course on it.
    """
    course_recommendations = set(decision.recommended_course_codes)
    for block in decision.blocks:
        if block.kind != "judgment":
            continue
        named_by_reads = {code for e in block.evidence_ids if e in records
                          and not any(ref.startswith("h_") for ref in records[e].entity_refs)
                          for code in canonical_codes_in(records[e].exact_text)
                          if code not in records[e].entity_refs}
        course_recommendations.update(
            code for code in canonical_codes_in(block.text) if code not in named_by_reads)
    return course_recommendations


def recommendation_problems(decision, candidates, refs, course_recommendations) -> list[str]:
    """A recommendation needs a judgment and its candidates' programs read.

    A candidate with no program contributes its courses to ``course_recommendations``,
    which is updated in place for the checks that follow.
    """
    problems = []
    if decision.outcome == "recommendation":
        if not any(block.kind == "judgment" for block in decision.blocks):
            problems.append("recommendation requires a judgment block")
        for cid in decision.candidate_ids:
            candidate = candidates.get(cid)
            if candidate is None:
                continue
            for handle in candidate.program_handles:
                if handle not in refs:
                    problems.append(f"candidate {cid}: read and cite program {handle}")
            if not candidate.program_handles:
                course_recommendations.update(candidate.course_codes)
    return problems


def unread_course_problems(decision, refs, course_recommendations) -> list[str]:
    """Each advised course must be a cited course read, or disclosed as missing."""
    problems = []
    for requested in course_recommendations:
        code = normalize_code(requested)
        if code is None:
            problems.append(f"invalid recommended course code {requested!r}")
        elif code in refs:
            continue  # A cited course read, not merely a program mention.
        elif not any(
            b.kind == "limitation" and code in b.text
            and re.search(r"missing|unavailable|unknown|not read", b.text, re.IGNORECASE)
            for b in decision.blocks
        ):
            problems.append(f"read recommended course {code} or disclose its missing record")
    return problems
