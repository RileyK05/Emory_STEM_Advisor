"""Term plans and what-if comparisons, ordered by the deterministic planner."""

from __future__ import annotations

import re

from app.advising.catalog import (
    load_advising_context,
)
from app.advising.comparison import match_reported_courses
from app.advising.research_repository import (
    NotInScope,
    ProgramItem,
    SupportedText,
    read_courses,
    read_program,
)
from app.advising.sequence import CourseRequirement, SequencePlan, pack_terms, plan_sequence
from app.artifacts.advising_summary import label_absent_codes_in
from app.atlas.display import canonical_code
from app.atlas.requisites import AllOf, AnyOf, CourseRef, canonical_codes_in
from app.db import queries
from app.generation.artifact_visuals import (
    WHATIF_SECTION,
    credit_chart,
    plan_diagram,
    whatif_blocks,
)
from app.generation.native_tools import (
    OutlineColumn,
    OutlineSection,
    OutlineSheet,
    StageOutline,
    StageSection,
)
from app.generation.pipeline.shared import (
    _BLOCK_ADAPTER,
    _CREDITS_IN_RECORD,
    _MAX_GROUNDED_COURSES,
    _MAX_PROGRAM_ITEMS,
    AssembledSection,
    _block_text,
    _check_program_kind,
    _program_plan_codes,
    _slot_from_chunk,
    _slot_refs,
    _stated_credits,
)
from app.generation.student_request import (
    NO_CHEM_RE,
    PREMED_RE,
    completed_from_text,
    premed_courses,
    requested_course_cap,
    requested_credit_cap,
    requested_terms,
    strict_term_limit,
)
from app.programs.naming import (
    program_named_in,
    programs_named_in,
)
from app.retrieval.types import CitedChunk


def _credits_from_record(text: str) -> float:
    match = _CREDITS_IN_RECORD.search(text or "")
    if match is None:
        return 3.0
    values = [float(value) for value in match.groups() if value]
    return max(values) if values else 3.0


def _longest_chain(prerequisites, courses: set[str]) -> list[str]:
    """The longest run of courses where each is a prerequisite of the next."""
    needs: dict[str, list[str]] = {}
    for course, prerequisite in prerequisites:
        if course in courses and prerequisite in courses:
            needs.setdefault(course, []).append(prerequisite)
    best: dict[str, list[str]] = {}

    def chain_to(course: str, seen: frozenset[str]) -> list[str]:
        if course not in best:
            longest: list[str] = []
            for prerequisite in sorted(needs.get(course, ())):
                if prerequisite not in seen:
                    found = chain_to(prerequisite, seen | {course})
                    if len(found) > len(longest):
                        longest = found
            best[course] = [*longest, course]
        return best[course]

    return max((chain_to(course, frozenset()) for course in sorted(courses)),
               key=len, default=[])


def _tree_codes(node) -> list[str]:
    if isinstance(node, CourseRef):
        return [node.code]
    if isinstance(node, (AllOf, AnyOf)):
        return [code for child in node.items for code in _tree_codes(child)]
    return []


class PlanStages:
    """Term plans and what-if comparisons, ordered by the deterministic planner. A mixin of ArtifactPipeline."""

    def _apply_plan(self, value: StageOutline):
        raw_selected = [str(code).strip() for code in value.selected_courses if str(code).strip()]
        # The outline's completed_courses are the model's reading; only courses the
        # student's own turns call done are left out of the plan.
        completed = list(dict.fromkeys(
            code for code in completed_from_text(
                self.student_text, self.known_courses, self.campus_equivalents)
            if self._course_exists(code)
        ))
        completed_all = set(completed)
        for code in completed:
            completed_all.update(self.campus_equivalents.get(code, frozenset()))
        model_selected = list(dict.fromkeys(
            code for code in (canonical_code(c) for c in raw_selected)
            if self._course_exists(code) and code not in completed_all
        ))
        if not value.program:
            raise ValueError("a term plan needs the program name")
        titles = []
        allowed: set[str] = set()
        first_allowed: set[str] = set()
        program_ids: list = []
        for requested_program in dict.fromkeys(
                [value.program, *value.programs]):
            title, program_id, program_allowed = self._match_program(requested_program)
            if not program_allowed:
                raise ValueError(
                    f"program {requested_program!r} was not found; use one of the supplied "
                    "program titles")
            _check_program_kind(self.brief, title)
            titles.append(title)
            allowed |= program_allowed
            program_ids.append(program_id)
            if not first_allowed:
                first_allowed = program_allowed
        title = " and ".join(dict.fromkeys(titles))[:200]
        allowed_all = set(allowed)
        for code in allowed:
            allowed_all.update(self.campus_equivalents.get(code, frozenset()))
        explicit_selected = [
            code for code in canonical_codes_in(self.request_text)
            if self._course_exists(code) and code not in completed_all
        ]
        selected = model_selected
        if not explicit_selected and (
            not completed
            or programs_named_in(self.request_text, self.program_titles)
            or requested_terms(self.request_text) is not None
        ):
            # A full plan from scratch (or with completed courses/named programs):
            # derive the core sequence deterministically from the program records.
            req_terms = requested_terms(self.request_text)
            target = max(16, req_terms * 4 if req_terms else 16)
            planned_from_program: list[str] = []
            for program_id in program_ids:
                if program_id is None:
                    continue
                items = self.conn.execute(
                    queries.get("list_program_items"), {"program_id": program_id}
                ).fetchall()
                for code in _program_plan_codes(items, self._course_exists, target=target):
                    if code not in planned_from_program:
                        planned_from_program.append(code)
            self.conn.commit()
            if planned_from_program:
                extra_valid = [
                    code for code in model_selected
                    if code in allowed_all and code not in planned_from_program
                ]
                selected = [*planned_from_program, *extra_valid]
        if PREMED_RE.search(self.request_text + "\n" + self.brief):
            selected = list(dict.fromkeys([
                *selected,
                *premed_courses(self._course_exists, self.campus_equivalents),
            ]))
        if NO_CHEM_RE.search(self.request_text):
            selected = [code for code in selected if not code.startswith(("CHEM ", "CHEM_"))]
        selected = [
            code for code in selected
            if code not in completed_all
        ][:_MAX_GROUNDED_COURSES]
        if not selected:
            raise ValueError("a term plan needs selected_courses that exist in this collection")
        self.extra_courses = set()
        for code in selected:
            if not self._course_exists(code):
                raise ValueError(f"{code} is not a course in this collection")
            if code not in first_allowed and not (
                    self.campus_equivalents.get(code, frozenset()) & first_allowed):
                # A premed course, a minor or a second major is not on the first
                # program's lines. It is still a real course the student named.
                self.extra_courses.add(code)
        plan = self._plan_sequence(selected, completed)
        # A selected course's prerequisites are part of the plan even when the
        # outline did not name them; they were read into `courses` above.
        planned_codes = [code for stage in plan.stages for code in stage]
        selected = list(dict.fromkeys([*selected, *planned_codes]))
        self.extra_courses.update(
            code for code in planned_codes
            if code not in first_allowed and not (
                self.campus_equivalents.get(code, frozenset()) & first_allowed))
        self._replace_plan_shape(value, plan, selected, completed)
        # The program's record names the page; a model-written title could invent a focus.
        value.title = f"{title}: Term plan"[:200]
        self.plan = plan
        self.program_title = titles[0]
        self.program_allowed = allowed

    def _apply_whatif(self, value: StageOutline):
        """Compare programs by which of their lines name the student's courses.

        The model supplies only program names and reported courses. The chart
        counts and line tables come from `match_reported_courses`, so they say
        where a course is named, never that a requirement is met.
        """
        requested = [name for name in dict.fromkeys([value.program, *value.programs]) if name]
        if not requested:
            named = program_named_in(self.request_text, self.program_titles)
            requested = [named] if named else []
        if not requested:
            raise ValueError("a what-if comparison needs at least one program; copy its exact "
                             "title from the supplied programs list into programs")
        completed = list(dict.fromkeys(
            code for code in (canonical_code(str(raw).strip())
                              for raw in value.completed_courses if str(raw).strip())
            if self._course_exists(code)))
        if not completed:
            raise ValueError("a what-if comparison needs the courses the student has taken in "
                             "completed_courses")
        scope = self._research_scope()
        if scope is None:
            raise ValueError("a what-if comparison needs grounded program sources")
        results, program_slots, titles = [], [], []
        for name in requested[:4]:
            title, program_id, _allowed = self._match_program(name)
            if program_id is None:
                raise ValueError(f"program {name!r} was not found; use one of the supplied "
                                 "program titles")
            _check_program_kind(self.brief, title)
            try:
                items = read_program(self.conn, scope, program_id).items[:_MAX_PROGRAM_ITEMS]
            except NotInScope:
                # The program's source is outside the selected sources: match its
                # stored lines, as the term planner does, without citing them.
                self.conn.rollback()
                items = tuple(
                    ProgramItem(row["position"], row["section"], SupportedText(row["text"]),
                                tuple(row["courses"]), row["choose"])
                    for row in self.conn.execute(queries.get("list_program_items"),
                                                 {"program_id": program_id}).fetchall()
                )[:_MAX_PROGRAM_ITEMS]
            self.conn.commit()
            matches, unused = match_reported_courses(items, completed, self.campus_equivalents)
            results.append((title, matches, unused))
            program_slots.append(list(dict.fromkeys(
                self._slot_for_chunk(chunk, scope)
                for item in items for chunk, _start, _end in item.line.support)))
            titles.append(title)
        heading = {"id": f"{WHATIF_SECTION}:h", "type": "heading", "level": 2,
                   "text": "If you switched programs"}
        # A catalog line can still print a code the Atlas lacks; the line stays
        # as the program wrote it and says which code cannot be found.
        models = label_absent_codes_in(
            [_BLOCK_ADAPTER.validate_python(block)
             for block in [heading, *whatif_blocks(results, completed=completed)]],
            self.known_courses, self.campus_equivalents, self.catalog_aliases)
        blocks = [model.model_dump(mode="json") for model in models]
        evidence = {f"{WHATIF_SECTION}:t{index}": _slot_refs(slots, self.grounding.slots)
                    for index, slots in enumerate(program_slots, 1) if slots}
        section = OutlineSection(
            id=WHATIF_SECTION, heading="If you switched programs",
            purpose="Program lines your courses appear on.",
            block_types=["heading", "paragraph", "table"],
            source_slots=[slot for slots in program_slots for slot in slots][:50])
        value.title = ("What if: " + " vs ".join(titles))[:200]
        value.sections, value.sheets, value.charts = [section], [], []
        value.selected_courses, value.completed_courses = [], completed
        self.kept[WHATIF_SECTION] = AssembledSection(
            section=section, blocks=blocks, evidence=evidence,
            text="\n".join(_block_text(block) for block in blocks),
            # The chart, headings and the reported-course lines are code-built;
            # the line tables cite the program sources they quote.
            skip_elements={block["id"] for block in blocks if block["id"] not in evidence})
        self.whatif_programs = titles

    def _plan_map_block(self) -> dict | None:
        """The course map for a fresh or re-planned term plan, built from the planner."""
        if self.plan is None or self.plan_layout is None:
            return None
        stages, blocked = self.plan_layout
        titles = {}
        for code, slot in self.plan_records.items():
            header = self.grounding.slots[slot - 1].text.partition("\n")[0]
            titles[code] = header.split(": ", 1)[1] if ": " in header else ""
        needs = {code: set(_tree_codes(requirement.prerequisites))
                 for code, requirement in self.plan_requirements.items()}
        return plan_diagram(self.plan, stages, blocked=blocked,
                            completed=self._completed_courses(), titles=titles,
                            prerequisite_codes=needs, equivalents=self.campus_equivalents,
                            credits=self.plan_stated_credits)

    def _plan_visuals(self) -> list[dict]:
        """The course map and, when every credit is known, the credit load chart."""
        diagram = self._plan_map_block()
        if diagram is None:
            return []
        load = credit_chart(self.plan_layout[0], self.plan_stated_credits)
        return [diagram, *([load] if load else [])]

    def _plan_sequence(self, selected: list[str], completed: list[str]) -> SequencePlan:
        scope = self._research_scope()
        if scope is None:
            raise ValueError("a term plan needs grounded atlas sources")
        pending = list(selected)
        visited: set[str] = set()
        courses: dict[str, CourseRequirement] = {}
        # A research evidence slot can be a slice of the same chunk.
        slot_numbers = {(slot.chunk_id, slot.text): index
                        for index, slot in enumerate(self.grounding.slots, 1)}
        self.plan_records.clear()
        self.plan_credits.clear()
        self.plan_stated_credits.clear()
        self.plan_seasons.clear()
        self.plan_notes.clear()
        self.plan_skip_cells: set[tuple[str, int, int]] = set()
        while pending and len(visited) < _MAX_GROUNDED_COURSES:
            batch, pending = pending[:4], pending[4:]
            visited.update(batch)
            reads = read_courses(self.conn, scope, batch)
            self.conn.commit()
            for read in reads:
                header = f"{read.code}: {read.title}"
                record = next((chunk for chunk in read.chunks
                               if chunk.text.partition("\n")[0] == header), None)
                if record is None:
                    continue
                self.plan_credits[read.code] = _credits_from_record(record.text)
                if (stated := _stated_credits(record.text)) is not None:
                    self.plan_stated_credits[read.code] = stated
                key = (record.chunk_id, record.text)
                if key not in slot_numbers:
                    self.grounding.slots.append(_slot_from_chunk(record, scope))
                    slot_numbers[key] = len(self.grounding.slots)
                self.plan_records[read.code] = slot_numbers[key]
            chunks = [chunk for read in reads for chunk in read.chunks]
            cited = [
                CitedChunk(chunk.chunk_id, chunk.source_id, chunk.chunk_index,
                           scope.sources[chunk.source_id].filename, chunk.locator_label,
                           chunk.text, (), 0, 0, index + 1)
                for index, chunk in enumerate(chunks)
            ]
            context = load_advising_context(
                self.conn, collection_id=self.collection_id, chunks=cited)
            self.conn.commit()
            for course in context["courses"]:
                reason = course.get("blocker_reason")
                stored_status = course.get("stored_parse_status")
                if (reason == "description_requirement_unparsed"
                        and stored_status in {"none", "parsed"}):
                    status = stored_status
                    note = (f"{course['code']}'s catalog description has a requirement note; "
                            "check it with your advisor.")
                    if note not in self.plan_notes:
                        self.plan_notes.append(note)
                elif course["requirements_evidence_complete"]:
                    status = course["parse_status"]
                else:
                    status = "unparsed"
                # A grade threshold stays a blocker in the planner, so the plan
                # lists the course for advisor review rather than assuming it met.
                courses[course["code"]] = CourseRequirement(
                    course["code"], course["title"], status,
                    course["prerequisites"], course["corequisites"], course["requisites_raw"],
                )
                for dependency in _tree_codes(course["prerequisites"]):
                    if (dependency not in visited and dependency not in pending
                            and dependency not in completed):
                        pending.append(dependency)
                for dependency in _tree_codes(course["corequisites"]):
                    if (dependency not in visited and dependency not in pending
                            and dependency not in completed):
                        pending.append(dependency)
        season_rows = self.conn.execute(
            queries.get("research_course_offering_terms"),
            {"collection_id": self.collection_id,
             "codes": sorted(set(courses) | set(selected))},
        ).fetchall()
        self.conn.commit()
        for row in season_rows:
            season = str(row["term"] or "").split(" ", 1)[0].casefold()
            if season:
                self.plan_seasons[row["code"]] = (
                    self.plan_seasons.get(row["code"], frozenset()) | {season})
        plan_selected = selected
        self.plan_requirements = courses
        return plan_sequence(courses, plan_selected, completed, self.campus_equivalents)

    def _workbook_plan_credits(self) -> bool:
        return bool(self.plan_stated_credits) or bool(
            re.search(r"\b(?:credits?|hours?|totals?)\b", self.request_text, re.IGNORECASE))

    def _replace_plan_shape(self, value: StageOutline, plan: SequencePlan,
                            selected: list[str], completed: list[str]):
        blocked = sorted({blocker.code for blocker in plan.blockers})
        reasons = []
        for blocker in plan.blockers:
            reason = blocker.reason.replace("_", " ")
            reasons.append(f"{blocker.code}: {reason}")
        asked_terms = requested_terms(self.request_text)
        packed = pack_terms(
            plan, credits=self.plan_credits, seasons=self.plan_seasons,
            completed=completed, max_credits=requested_credit_cap(self.request_text),
            max_courses=requested_course_cap(self.request_text),
            max_terms=asked_terms or 8,
        )
        if packed.unscheduled and not strict_term_limit(self.request_text):
            # A descriptive "four-semester plan" can need more terms for a full
            # program; a student who said "finish in four semesters" cannot.
            packed = pack_terms(
                plan, credits=self.plan_credits, seasons=self.plan_seasons,
                completed=completed, max_credits=requested_credit_cap(self.request_text),
                max_courses=requested_course_cap(self.request_text), max_terms=12,
            )
        if packed.unscheduled:
            limit = asked_terms or 12
            raise ValueError(
                f"these courses cannot fit in {limit} terms at this credit cap: "
                + ", ".join(packed.unscheduled)
                + "; ask for more terms or a lighter load")
        if not packed.terms and not blocked:
            raise ValueError("no selected course could be scheduled; explain why and ask "
                             "what the student wants to change")
        if len(selected) > 3 and packed.terms and len(packed.terms) < 2:
            raise ValueError("a term plan needs at least two terms; include the rest of "
                             "the program's courses")
        stages = [list(term) for term in packed.terms]
        if asked_terms and len(stages) > asked_terms:
            # "A four-semester plan" is a description, so a program that cannot fit
            # gets more terms; the student is told why instead of finding a Term 5.
            chain = _longest_chain(plan.prerequisites, {code for stage in stages for code in stage})
            reason = (f"; {' → '.join(chain)} alone is {len(chain)} courses taken one after "
                      "another" if len(chain) > asked_terms else " at this credit load")
            note = (f"You asked for {asked_terms} terms; these courses need "
                    f"{len(stages)}{reason}.")
            if note not in self.plan_notes:
                self.plan_notes.append(note)
        self.plan_layout = (stages, blocked)
        parts =[(f"term{index}", f"Term {index}", stage, [])
                 for index, stage in enumerate(stages, 1)]
        if blocked:
            parts.append(("advisor", "Check with your advisor", blocked, reasons))
        if len(parts) > self.settings.artifact_max_sections:
            raise ValueError(f"this plan needs {len(parts)} terms and advisor sections; shorten it")
        value.sections = []
        value.sheets = []
        value.charts = []
        for section_id, heading, codes, blockers in parts:
            missing = [code for code in codes if code not in self.plan_records]
            if missing:
                raise ValueError("no in-scope course record for " + ", ".join(missing))
            fields = {"id": section_id, "courses": codes, "blocked_reasons": blockers,
                      "source_slots": [self.plan_records[code] for code in codes]}
            purpose = "Courses requiring advisor review" if blockers else "Scheduled courses"
            if self.kind == "workbook":
                columns = [OutlineColumn(key="course", header="Course")]
                if self._workbook_plan_credits() and not blockers:
                    columns.append(OutlineColumn(key="credits", header="Credits", type="number"))
                value.sheets.append(OutlineSheet(
                    name=heading, row_purpose=purpose, columns=columns, **fields))
            else:
                value.sections.append(OutlineSection(
                    heading=heading, purpose=purpose, block_types=["list", "paragraph"], **fields))
        value.selected_courses = selected
        value.completed_courses = completed

    def _write_plan_section(self, section, visible, repair, lane):
        # The schedule, its citations and the course map are all code-built. A
        # model call per term only ever added an optional copied sentence, at the
        # cost of resending the term's course records, so none is made.
        lines = []
        for code in section.courses:
            slot = self.plan_records[code]
            title = self.grounding.slots[slot - 1].text.splitlines()[0].split(": ", 1)[1]
            line = f"{code} — {title}"
            prefix = f"{code}: "
            reasons = [entry.removeprefix(prefix) for entry in section.blocked_reasons
                       if entry.startswith(prefix)]
            if reasons:
                line += " (" + ", ".join(dict.fromkeys(reasons)) + ")"
            lines.append((code, line, slot))

        if self.kind == "workbook":
            include_credits = self._workbook_plan_credits() and not section.blocked_reasons
            skip_cells = getattr(self, "plan_skip_cells", set())
            rows = []
            for row_index, (code, line, slot) in enumerate(lines, 2):
                if include_credits:
                    raw_cr = self.plan_stated_credits.get(code, 4)
                    cr = int(raw_cr) if raw_cr == int(raw_cr) else raw_cr
                    if code not in self.plan_stated_credits:
                        skip_cells.add((section.id, row_index, 2))
                    rows.append({"values": [line, cr], "source_slots": [slot]})
                else:
                    rows.append({"values": [line], "source_slots": [slot]})
            self.plan_skip_cells = skip_cells
            draft = StageSection(rows=rows)
        else:
            blocks = [{"id": f"course{index}", "type": "list", "items": [line]}
                      for index, (_code, line, _slot) in enumerate(lines, 1)]
            bindings = [{"element_id": block["id"], "source_slots": [slot]}
                        for block, (_code, _line, slot) in zip(blocks, lines, strict=True)]
            draft = StageSection(blocks=blocks, bindings=bindings)
        self.section_payloads[section.id] = draft

    def _completed_courses(self) -> list[str]:
        if self.outline is not None and self.outline.completed_courses:
            return list(self.outline.completed_courses)
        return list(self._prior_completed)

    def _plan_assumptions_block(self) -> dict | None:
        if self.plan is None:
            return None
        total_credits = sum(
            self.plan_credits.get(code, 3.0)
            for stage in self.plan.stages
            for code in stage
        )
        total_label = (
            str(int(total_credits))
            if total_credits == int(total_credits)
            else f"{total_credits:.1f}"
        )
        cap = requested_credit_cap(self.request_text)
        cap_label = str(int(cap)) if cap == int(cap) else f"{cap:.1f}"
        # The terms the file shows, not the planner's prerequisite stages.
        shown_terms = len(self.plan_layout[0]) if self.plan_layout else len(self.plan.stages)
        parts = [
            (
                f"Scheduled credits shown: {total_label} across {shown_terms} term(s) "
                f"(up to {cap_label} credits per term)."
            )
        ]
        completed = self._completed_courses()
        if completed:
            parts.append(
                "Completed courses excluded from remaining terms: "
                + ", ".join(completed)
                + "."
            )
        if getattr(self, "plan_notes", None):
            parts.extend(self.plan_notes)
        return {
            "id": "assumptions:p",
            "type": "paragraph",
            "text": "Plan assumptions: " + " ".join(parts),
        }
