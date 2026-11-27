"""What a reply may say about a file still being made by the artifact pipeline."""

import re

from app.generation.answer_checks.safety import _safety_variants

_ARTIFACT_READY = re.compile(
    r"\bhere(?:['’]s| is| are) your (?:[a-z]+\s+){0,3}"
    r"(?:plan|checklist|document|spreadsheet|workbook|sheet|page|report|comparison|handout)\b"
    r"|\b(?:the|your) (?:[a-z]+\s+){0,3}"
    r"(?:plan|checklist|document|spreadsheet|workbook|sheet|page|report|comparison|handout)\s+"
    r"(?:is|are) (?:now )?(?:ready|attached)\b"
    # Past tense too: "I made you a spreadsheet", "I've corrected the tracker" were
    # published before an edit that then failed, and the file never changed.
    r"|\bI(?:['’]ve| have)? (?:just )?(?:made|created|built|attached|prepared|put together|"
    r"corrected|updated|changed|fixed|revised|edited) "
    r"(?:your |the )?(?:[a-z]+\s+){0,3}"
    r"(?:plan|checklist|document|spreadsheet|workbook|sheet|tracker|page|report|comparison|"
    r"handout)\b"
    # "Done — the physiology line is out" was published before the edit failed.
    r"|^\s*done\b\s*[—–:!,.-]"
    r"|\b(?:line|row|item|entry)s?\s+(?:is|are|has\s+been|have\s+been)\s+"
    r"(?:now\s+)?(?:out|gone|removed|deleted|added|updated)\b"
    r"|\b(?:course|section|column)s?\s+(?:has|have)\s+been\s+(?:removed|deleted|added)\b"
    r"|\bno\s+longer\s+(?:has|lists|includes|shows)\b",
    re.IGNORECASE | re.MULTILINE)


_DONE_CLAIM = re.compile(
    r"\balready\s+(?:done|taken|completed|finished|behind\s+you|on\s+(?:your|the)\s+record)\b"
    r"|\b(?:are|is|were)\s+(?:all\s+)?(?:done|finished|completed|taken|behind\s+you)\b"
    r"|\bon\s+(?:your|the)\s+record\b", re.IGNORECASE)


# "Assumes nothing is done" is the honest version, not a guess.
_NOTHING_DONE = re.compile(r"\b(?:nothing|none|no\s+courses?)\b", re.IGNORECASE)


_GUESS = re.compile(
    r"\bassum\w*|\bpresum\w*|\b(?:didn['’]t|did\s+not|haven['’]t|have\s+not)\s+"
    r"(?:say|said|tell|told|mention|mentioned)\b", re.IGNORECASE)


# "Term 2: CS 371W", "Terms 3 and 4: ..."; "a fifth term" is a count, not a schedule.
_TERM_SCHEDULE = re.compile(r"\bterms?\s+\d\b", re.IGNORECASE)


_COURSE_NUMBER = re.compile(r"\b\d{3}[A-Z]?\b")


# A course by number, or by name ("the two calculus courses").
_COURSE_MENTION = re.compile(r"\b\d{3}[A-Z]?\b|\bcourses?\b|\bclasses?\b|\bcalculus\b",
                             re.IGNORECASE)


def _assumes_courses_done(text: str) -> bool:
    """A sentence that guesses which courses are done ("assumes CS 170 is already done")."""
    return any(_GUESS.search(sentence) and _DONE_CLAIM.search(sentence)
               and _COURSE_MENTION.search(sentence) and not _NOTHING_DONE.search(sentence)
               for sentence in re.split(r"(?<=[.!?])\s+", text))


_ARTIFACT_PROGRESS = re.compile(
    r"\b(?:the|your) (?:[a-z]+\s+){0,3}"
    r"(?:plan|checklist|document|spreadsheet|workbook|sheet|page|report|comparison|handout)\s+"
    r"(?:is|are)\s+being\s+(?:made|written|prepared|created|built)\b"
    r"|\bI(?:['’]m| am)\s+(?:making|writing|preparing|creating|building)\b"
    r"|\b(?:are\s+now|is\s+now|have\s+been|has\s+been)\s+"
    r"(?:ticked\s+off|checked\s+off|crossed\s+off|marked\s+(?:as\s+)?(?:done|complete|completed)"
    r"|updated\s+in\s+(?:your|the)\s+(?:checklist|tracker|plan|sheet|table|workbook))\b",
    re.IGNORECASE)


def file_claim_problems(decision, *, artifact_expected: bool = False) -> list[str]:
    """Reject a reply that misstates the file being made: ready, assumed, scheduled, or unasked."""
    problems = []
    variants = _safety_variants("\n".join(block.text for block in decision.blocks))
    # The answer is published before the artifact run starts, and that run can fail.
    if ((decision.artifact is not None or artifact_expected)
            and any(_ARTIFACT_READY.search(variant) for variant in variants)):
        problems.append("the reply says the artifact is ready; it is still being made, so say "
                        "it is being made")
    # The file counts as done only what the student said they finished (the artifact
    # pipeline's rule); an answer that guesses more promises a plan the file is not.
    if ((decision.artifact is not None or artifact_expected)
            and any(_assumes_courses_done(variant) for variant in variants)):
        problems.append("the reply assumes courses are done that the student did not say "
                        "they finished; the file plans from the start and counts only what "
                        "the student said, so do not assume any course is done, say how many "
                        "terms the prerequisites need, and ask what they have finished")
    # The file is built after this reply and numbers its own terms; a schedule written
    # here would disagree with it ("Term 2: CS 371W" against a file's Term 5).
    if ((decision.artifact is not None or artifact_expected)
            and any(_TERM_SCHEDULE.search(sentence) and _COURSE_NUMBER.search(sentence)
                    for variant in variants
                    for sentence in re.split(r"(?<=[.!?])\s+", variant))):
        problems.append("the reply lays out its own term schedule; the file numbers the "
                        "terms, so explain the order and priorities without Term 1, Term 2")
    if (decision.artifact is None and not artifact_expected
            and any(_ARTIFACT_PROGRESS.search(variant) for variant in variants)):
        problems.append("the reply says an artifact is being made, but no artifact was "
                        "requested; make the request or answer without promising one")
    return problems
