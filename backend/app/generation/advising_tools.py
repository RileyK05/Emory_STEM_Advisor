"""Read-only native advising tools over the current answer's evidence snapshot."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.records import normalize_code
from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, Req
from app.retrieval.types import CitedChunk
from app.tools.registry import (
    ToolCapability,
    ToolInputModel,
    ToolOutputModel,
    ToolSpec,
)

ADVISING_TOOLS_VERSION = "2"


def _course_code(value: str) -> str:
    code = normalize_code(value)
    if code is None:
        raise ValueError("invalid atlas course code")
    return code


CourseCode = Annotated[str, Field(min_length=1, max_length=32), AfterValidator(_course_code)]


class EvidenceInput(ToolInputModel):
    resource_ids: list[str] = Field(min_length=1, max_length=100)


class RequirementsInput(EvidenceInput):
    course_codes: list[CourseCode] = Field(min_length=1, max_length=50)


class SequenceInput(EvidenceInput):
    selected_codes: list[CourseCode] = Field(min_length=1, max_length=50)
    completed_codes: list[CourseCode] = Field(default_factory=list, max_length=100)


class RequirementNode(ToolOutputModel):
    node_type: Literal["all", "any", "course", "condition"]
    course_code: str | None = None
    condition: str | None = None
    children: list[RequirementNode] = Field(default_factory=list)


class CourseEvidence(ToolOutputModel):
    code: str
    title: str
    lifecycle: Literal["current", "retired"] = "current"
    citation_numbers: list[int]


class RequirementHoldEvidence(ToolOutputModel):
    hold_id: str
    reason: str
    master_file_hash: str
    evidence_capture_sha256: str
    evidence_capture_path: str
    evidence_source_id: str | None = None
    evidence_index_revision: int | None = None
    evidence_capture_set_hash: str | None = None


class RequirementsEvidence(CourseEvidence):
    parse_status: str
    requirements_evidence_complete: bool
    prerequisites: RequirementNode | None
    corequisites: RequirementNode | None
    requisites_raw: str | None
    blocker_reason: str | None
    requirement_hold: RequirementHoldEvidence | None = None


class Blocker(ToolOutputModel):
    code: str
    reason: str
    alternatives: list[str] = Field(default_factory=list)


class RequirementsOutput(ToolOutputModel):
    courses: list[RequirementsEvidence]
    blockers: list[Blocker]


class TopicGroup(ToolOutputModel):
    cluster_id: str
    path: list[str]
    total_courses: int
    supported_courses: list[CourseEvidence]
    evidence_complete: bool


class TopicsOutput(ToolOutputModel):
    grouping: Literal["learned_department_topics"] = "learned_department_topics"
    groups: list[TopicGroup]


class SequenceOutput(ToolOutputModel):
    stages: list[list[str]]
    blockers: list[Blocker]
    complete: bool
    scope: Literal["prerequisite_order_only"] = "prerequisite_order_only"
    enrollment_eligibility_verified: Literal[False] = False
    assumed_completed_codes: list[str]
    evidence: list[CourseEvidence]


def _tree(req: Req | None, depth: int = 1) -> RequirementNode | None:
    if req is None:
        return None
    if depth > 64:
        raise ValueError("requisite tree exceeds supported depth")
    if isinstance(req, CourseRef):
        return RequirementNode(node_type="course", course_code=req.code)
    if isinstance(req, Condition):
        return RequirementNode(node_type="condition", condition=req.text)
    if isinstance(req, (AllOf, AnyOf)):
        return RequirementNode(node_type="all" if isinstance(req, AllOf) else "any",
                               children=[_tree(item, depth + 1) for item in req.items])
    raise ValueError("unsupported requisite tree")


def _evidence(course: dict) -> CourseEvidence:
    return CourseEvidence(code=course["code"], title=course["title"],
                          lifecycle=course.get("lifecycle", "current"),
                          citation_numbers=list(course["citation_numbers"]))


def _requirements(course: dict) -> RequirementsEvidence:
    return RequirementsEvidence(
        **_evidence(course).model_dump(), parse_status=course["parse_status"],
        requirements_evidence_complete=course["requirements_evidence_complete"],
        prerequisites=_tree(course["prerequisites"]),
        corequisites=_tree(course["corequisites"]), requisites_raw=course["requisites_raw"],
        blocker_reason=course["blocker_reason"],
        requirement_hold=(
            RequirementHoldEvidence(
                **{
                    **course["requirement_hold"],
                    "hold_id": str(course["requirement_hold"]["hold_id"]),
                    "evidence_source_id": (
                        str(course["requirement_hold"]["evidence_source_id"])
                        if course["requirement_hold"]["evidence_source_id"] is not None else None
                    ),
                }
            ) if course.get("requirement_hold") is not None else None
        ),
    )


def advising_identity(context: dict | None) -> dict | None:
    """Include graph and requirement facts in the durable tool replay identity."""
    if context is None:
        return None
    return {
        "version": ADVISING_TOOLS_VERSION,
        "courses": [_requirements(course).model_dump(mode="json")
                    for course in context["courses"]],
        "topic_groups": [
            {"cluster_id": str(group["cluster_id"]), "path": list(group["path"]),
             "total_size": group["total_size"], "course_codes": list(group["course_codes"])}
            for group in context["topic_groups"]
        ],
    }


def advising_specs(context: dict | None, chunks: list[CitedChunk]) -> tuple[ToolSpec, ...]:
    if not context or not context["courses"]:
        return ()
    citation_resources = {number: str(chunk.chunk_id)
                          for number, chunk in enumerate(chunks, 1)}

    def supported(resource_ids: list[str]) -> dict[str, dict]:
        allowed = set(resource_ids)
        # Require every excerpt used to establish a course's facts. A narrower
        # tool call cannot inherit requisite evidence from omitted resources.
        return {course["code"]: course for course in context["courses"]
                if course["citation_numbers"] and all(
                    citation_resources.get(number) in allowed
                    for number in course["citation_numbers"])}

    def topics(value, _scope):
        courses = supported(value.resource_ids)
        groups = []
        for group in context["topic_groups"]:
            members = [courses[code] for code in group["course_codes"] if code in courses]
            if members:
                groups.append(TopicGroup(
                    cluster_id=str(group["cluster_id"]), path=list(group["path"]),
                    total_courses=group["total_size"],
                    supported_courses=[_evidence(course) for course in members],
                    evidence_complete=len(members) == group["total_size"],
                ))
        return TopicsOutput(groups=groups)

    def requirements(value, _scope):
        courses = supported(value.resource_ids)
        codes = sorted(set(value.course_codes))
        return RequirementsOutput(
            courses=[_requirements(courses[code]) for code in codes if code in courses],
            blockers=[Blocker(code=code, reason="course_evidence_missing")
                      for code in codes if code not in courses],
        )

    def sequence(value, _scope):
        courses = supported(value.resource_ids)
        records = {
            code: CourseRequirement(
                code=code, title=course["title"], parse_status=course["parse_status"],
                prerequisites=course["prerequisites"], corequisites=course["corequisites"],
                requisites_raw=course["requisites_raw"],
                lifecycle=course.get("lifecycle", "current"),
            ) for code, course in courses.items()
        }
        plan = plan_sequence(records, value.selected_codes, value.completed_codes)
        blockers = []
        for item in plan.blockers:
            reason = item.reason
            if reason == "unknown_course":
                reason = "course_evidence_missing"
            elif reason == "requirements_unparsed" and item.code in courses:
                reason = courses[item.code]["blocker_reason"] or reason
            blockers.append(Blocker(code=item.code, reason=reason,
                                    alternatives=list(item.alternatives)))
        return SequenceOutput(
            stages=[list(stage) for stage in plan.stages], blockers=blockers,
            complete=plan.complete, assumed_completed_codes=sorted(set(value.completed_codes)),
            evidence=[_evidence(courses[code]) for code in sorted(set(value.selected_codes))
                      if code in courses],
        )

    definitions = (
        ("advisor_explore_topics", EvidenceInput, TopicsOutput, topics,
         ("Explore learned department/topic groups represented by the current numbered excerpts. "
         "Pass opaque chunk resource_ids from the context. Only cited members are returned; "
         "evidence_complete=false means this is a partial view of the full group. "
         "Topic similarity is exploratory and never establishes prerequisites or degree eligibility.")),
        ("advisor_read_course_requirements", RequirementsInput, RequirementsOutput, requirements,
         ("Read deterministic atlas prerequisite/corequisite trees for cited course_codes. "
         "Pass the supporting chunk resource_ids. Missing or incompletely cited requisites are "
         "blocked, never inferred. Cite the returned current citation_numbers.")),
        ("advisor_plan_course_sequence", SequenceInput, SequenceOutput, sequence,
         ("Order explicitly selected courses using deterministic AND/OR prerequisites and concurrent "
         "corequisites. Pass supporting chunk resource_ids. Use completed_codes only for courses "
         "the student explicitly says they completed; results are conditional on these unverified "
         "assumptions. Explain every blocker. Stages are dependency order, not semesters, degree "
         "approval, availability, credit-load advice, or verified enrollment eligibility.")),
    )
    return tuple(ToolSpec(
        tool_id=name, description=description, input_model=input_model, output_model=output_model,
        capability=ToolCapability.SOURCE_READ, handler=handler, resource_fields=("resource_ids",),
    ) for name, input_model, output_model, handler, description in definitions)
