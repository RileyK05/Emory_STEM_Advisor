"""Validate research references and render server-owned citation numbers."""

import re
from dataclasses import dataclass
from uuid import UUID

from app.advising.research_types import (
    DecisionBlock,
    EvidenceRecord,
    FinalDecision,
    ResearchState,
    validate_candidate_references,
    validate_decision_limits,
    validate_evidence_references,
    validate_fact_support,
)
from app.atlas.records import normalize_code
from app.atlas.requisites import canonical_codes_in
from app.generation.answer import NONSUPPORT_RE, Citation, GeneratedAnswer
from app.llm.base import Usage
from app.retrieval.types import CitedChunk

RESEARCH_PROMPT_VERSION = "research-48"
RESEARCH_RENDER_VERSION = "research-1"
_MARKER = re.compile(r"\[\s*(?:e\d+|\d+(?:\s*[,;-]\s*\d+)*)\s*\]", re.IGNORECASE)
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


_RETIRED_SAID = re.compile(r"no longer (?:offered|runs?|available)|not offered|retired|discontinued",
                           re.IGNORECASE)


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
            f"{', '.join(sorted(unsaid))} no longer offered: no Course Atlas term shows it. "
            "Drop it, or say it is no longer offered in the same sentence, and name the current "
            "course from the program list or requisites instead")
    return problems


# The whole reply to a request outside academic advising. It is fixed so a
# declined request never carries model-written text.
SCOPE_DECLINE_TEXT = ("I can only help with Emory academic advising: courses, majors, "
                      "requirements, policies, and planning for grad school or a career.")


class DecisionRejected(ValueError):
    pass


# A clause that names a course the master says does not exist must say so. The
# finish gate requires naming it, so this is the one wording that may.
_ABSENT_SAID = re.compile(
    r"\b(?:does(?: not|n['’]t) exist|no such (?:course|class)|"
    r"(?:is|are)(?: not|n['’]t) (?:a |an )?(?:current |real |valid )?(?:emory )?"
    r"(?:course|class)(?:es)?\b|there (?:is|are) no|"
    r"not (?:in|found in|listed in) the course atlas|no record of)", re.IGNORECASE)


# What an answer is made of, checked before what it claims. A model that is
# injected or misfires can return a stub, its own instructions, or text shaped
# like a secret; none of it may reach a student.
_STUB = re.compile(r"(?:placeholder|lorem ipsum|todo|tbd|n/?a|answer|text|none|null)?",
                   re.IGNORECASE)
_MIN_ANSWER_LETTERS = 12
_ECHO_SHINGLE = 8
_ECHO_LIMIT = 3
_SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}|\b[A-Z][A-Z0-9_]{2,}_(?:API_)?KEY\s*=|"
    r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"Traceback \(most recent call last\):|File \"[^\"]+\", line \d+")
_URL = re.compile(r"\bhttps?://[^\s)\]>\"']+|\bwww\.[^\s)\]>\"']+", re.IGNORECASE)
_CREDENTIAL_ASK = re.compile(
    r"\b(?:verify|enter|provide|send|confirm|share|submit|type)\b[^.!?\n]{0,60}"
    r"\b(?:password|passcode|credentials)\b", re.IGNORECASE)


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(words[i:i + _ECHO_SHINGLE]) for i in range(len(words) - _ECHO_SHINGLE + 1)}


def answer_content_problems(decision: FinalDecision, *, instructions: str | None,
                            evidence: list[EvidenceRecord]) -> list[str]:
    """Reject a reply that is empty, echoes the instructions, or carries a secret or link."""
    texts = [block.text for block in decision.blocks]
    problems = []
    if any(not re.search(r"[A-Za-z]", text) or _STUB.fullmatch(text.strip(" .,:;!?-*#\"'"))
           for text in texts) or sum(len(re.findall(r"[A-Za-z]", t)) for t in texts) < (
               _MIN_ANSWER_LETTERS):
        problems.append("a block has no answer in it; write the answer itself in plain sentences")
    written = "\n".join(texts)
    if instructions and len(_shingles(written) & _instruction_shingles(instructions)) >= (
            _ECHO_LIMIT):
        problems.append("the reply repeats the advisor's instructions; answer the student instead")
    if _SECRET.search(written):
        problems.append("the reply contains credential, key or error text; remove it")
    known = "\n".join(f"{record.source_name}\n{record.exact_text}" for record in evidence).lower()
    for url in _URL.findall(written):
        bare = re.sub(r"^https?://", "", url.rstrip(".,;:!?").lower()).rstrip("/")
        if bare not in known:
            problems.append("the reply links to a page no evidence names; remove the link")
            break
    if _CREDENTIAL_ASK.search(written):
        problems.append("the reply asks the student for a password or credentials; remove it")
    return problems


_INSTRUCTION_SHINGLES: dict[int, set[tuple[str, ...]]] = {}


def _instruction_shingles(instructions: str) -> set[tuple[str, ...]]:
    # The instructions are a few fixed strings; shingle each once.
    key = hash(instructions)
    if key not in _INSTRUCTION_SHINGLES:
        _INSTRUCTION_SHINGLES[key] = _shingles(instructions)
    return _INSTRUCTION_SHINGLES[key]


def validate_final(decision: FinalDecision, state: ResearchState | None,
                   evidence: list[EvidenceRecord], supplied: set[str],
                   checks: dict[str, dict], entity_evidence: dict[str, set[str]] | None = None,
                   artifact_references: set[tuple[str, str]] | None = None,
                   known_course_codes: frozenset[str] | None = None,
                   retired_courses: frozenset[str] = frozenset(),
                   instructions: str | None = None,
                   absent_codes: frozenset[str] = frozenset()) -> None:
    candidates = {c.id: c for c in state.candidates} if state else {}
    records = {e.evidence_id: e for e in evidence}
    problems = (validate_decision_limits(decision) + validate_fact_support(decision)
                + (answer_content_problems(decision, instructions=instructions,
                                           evidence=evidence) if decision.blocks else [])
                + validate_evidence_references(decision, supplied & records.keys())
                + validate_candidate_references(decision, set(candidates)))
    if not decision.blocks:
        problems.append("finish requires at least one answer block")
    if any(_MARKER.search(b.text) for b in decision.blocks):
        problems.append("block text contains a raw citation marker; use evidence_ids")
    for block in decision.blocks:
        if block.kind == "judgment" and (not block.candidate_ids or not block.evidence_ids):
            problems.append("judgment requires a candidate and supporting evidence; "
                            "use fact or limitation for source-scope conclusions, "
                            "and judgment only for candidate preferences")
    cited = {e for b in decision.blocks for e in b.evidence_ids}
    refs = {ref for e in cited if e in records for ref in records[e].entity_refs}
    if entity_evidence is not None:
        refs = {entity for entity, ids in entity_evidence.items() if ids & cited}
    # Two different questions, checked at two different levels.
    #
    # Identity: a course the answer names must be a course that exists. The
    # course master is the authority on existence, and the run's evidence is
    # the authority on what the run established: a course the run never read
    # is still a course, and an answer may say so, while a course that is in
    # no master and in no passage is not a course at all. That is a question
    # about the run and the collection, not about one block's citations: a
    # block may cite a narrow passage while naming a course the run read
    # elsewhere.
    #
    # Claim support: a passage must establish what a block asserts about a
    # course. That is a question about the passage the block cites.
    established: set[str] = set()
    for record in records.values():
        established |= set(canonical_codes_in(
            f"{record.locator_label}\n{record.exact_text}"))
    established |= _program_requirement_mentions(records, set(records))
    if known_course_codes:
        established |= set(known_course_codes)
    # Codes the student asked about that are not courses. Naming one is how an
    # answer says it does not exist, so a clause that says so is not an identity
    # claim; a clause that names one any other way treats it as a course.
    # A code a passage the run read names is real to this run, master or not.
    absent_codes = frozenset(absent_codes - established)
    unsaid_absent: set[str] = set()
    for block in decision.blocks:
        for clause in re.split(r"[;\n]|(?<=[.!?])\s+", block.text):
            if not _ABSENT_SAID.search(clause):
                unsaid_absent |= set(canonical_codes_in(clause)) & absent_codes
    if unsaid_absent:
        problems.append(
            f"{', '.join(sorted(unsaid_absent))} is not a course; say in the same sentence "
            "that it does not exist, and claim nothing else about it")
    established |= absent_codes
    for block in decision.blocks:
        if block.kind not in {"fact", "limitation"}:
            # A judgment compares candidates the run already read; a question
            # asks the student something. Neither asserts an institutional
            # fact about a course, so neither is an identity claim.
            continue
        missing = set()
        for clause in re.split(r"[;\n]|(?<=[.!?])\s+", block.text):
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
    for block in decision.blocks:
        if block.kind != "fact" or not _INCLUSION.search(block.text):
            continue
        # The locator is part of the cited passage: a course record's locator
        # names the course the passage is about.
        support = "\n".join(
            f"{records[e].locator_label}\n{records[e].exact_text}"
            for e in block.evidence_ids if e in records)
        present = set(canonical_codes_in(support)) | _program_requirement_mentions(
            records, set(block.evidence_ids)) | absent_codes
        missing = set()
        for clause in re.split(r"[;\n]|(?<=[.!?])\s+", block.text):
            named = set(canonical_codes_in(clause)) - present
            if not named or NONSUPPORT_RE.search(clause):
                continue
            missing.update(named)
        if missing:
            problems.append(
                "fact block names course code(s) absent from its cited evidence: "
                + ", ".join(sorted(missing)))
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
    course_recommendations = set(decision.recommended_course_codes)
    if decision.outcome == "recommendation":
        if not decision.candidate_ids or not any(b.kind == "judgment" for b in decision.blocks):
            problems.append("recommendation requires a candidate and a judgment block")
        for cid in decision.candidate_ids:
            candidate = candidates.get(cid)
            if candidate is None:
                continue
            for handle in candidate.program_handles:
                if handle not in refs:
                    problems.append(f"candidate {cid}: read and cite program {handle}")
            if not candidate.program_handles:
                course_recommendations.update(candidate.course_codes)
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
    for check_id in decision.preparation_check_ids:
        if check_id not in checks:
            problems.append(f"preparation check {check_id} was not executed")
    for claim in decision.preparation_claims:
        check = checks.get(claim.check_id)
        if (check is None or claim.check_id not in decision.preparation_check_ids
                or set(claim.selected_codes) != set(check["selected_codes"])
                or set(claim.completed_codes) != set(check["completed_codes"])
                or claim.conclusion != check["conclusion"]):
            problems.append(f"preparation claim does not match executed check {claim.check_id}")
        elif not set(check["evidence_ids"]) <= supplied:
            problems.append(f"reactivate evidence for preparation check {claim.check_id}")
    if decision.artifact is not None and decision.artifact.purpose == "revise":
        revises = decision.artifact.revises
        referenced = artifact_references or set()
        if revises is None or (str(revises.artifact_id), str(revises.revision_id)) not in referenced:
            problems.append("artifact revision was not referenced by this turn")
    problems += retired_course_problems(decision, course_recommendations, retired_courses)
    if problems:
        raise DecisionRejected("; ".join(problems))


@dataclass(frozen=True)
class RenderedDecision:
    answer: GeneratedAnswer
    chunks: list[CitedChunk]
    blocks: list[dict]


def render_final(decision: FinalDecision, evidence: list[EvidenceRecord], *,
                 system_prompt: str, user_prompt: str, usage: Usage,
                 model_id: str | None) -> RenderedDecision:
    records = {e.evidence_id: e for e in evidence}
    numbers: dict[str, int] = {}
    chunks, citations, blocks, paragraphs = [], [], [], []
    for block in decision.blocks:
        for eid in block.evidence_ids:
            if eid in numbers:
                continue
            record = records[eid]
            numbers[eid] = len(numbers) + 1
            chunks.append(CitedChunk(
                UUID(record.chunk_id), UUID(record.source_id), 0, record.source_name,
                record.locator_label, record.exact_text, (), 0.0, 0, len(numbers)))
            citations.append(Citation(record.source_name, record.locator_label,
                                      UUID(record.chunk_id), UUID(record.source_id)))
        refs = list(dict.fromkeys(numbers[e] for e in block.evidence_ids))
        text = block.text + (" " + " ".join(f"[{n}]" for n in refs) if refs else "")
        paragraphs.append(text)
        blocks.append({"kind": block.kind, "text": block.text,
                       "evidenceIds": list(block.evidence_ids), "citationNumbers": refs,
                       "candidateIds": list(block.candidate_ids)})
    warnings = ("Research is partial; unresolved questions remain.",) if (
        decision.completeness == "partial") else ()
    answer = GeneratedAnswer("\n\n".join(paragraphs), tuple(citations), system_prompt,
                             user_prompt, usage.prompt_tokens, usage.completion_tokens,
                             warnings, usage.source, model_id)
    return RenderedDecision(answer, chunks, blocks)


def declined_decision(decision: FinalDecision) -> FinalDecision:
    """The published form of outcome=declined: the fixed sentence and nothing else.

    A decline is not an answer, so it may not cite, weigh candidates, recommend
    courses or make an artifact; a proposal that does is answering and is sent
    back to finish as one.
    """
    if (decision.candidate_ids or decision.recommended_course_codes
            or decision.preparation_check_ids or decision.preparation_claims
            or decision.artifact is not None
            or any(block.evidence_ids or block.candidate_ids for block in decision.blocks)):
        raise DecisionRejected(
            "outcome=declined is for requests outside academic advising and carries no "
            "citations, candidates, courses or artifact; if this is advising, finish with "
            "outcome=information instead")
    return decision.model_copy(update={
        "completeness": "complete", "unresolved_questions": (),
        "blocks": (DecisionBlock(kind="limitation", text=SCOPE_DECLINE_TEXT),)})
