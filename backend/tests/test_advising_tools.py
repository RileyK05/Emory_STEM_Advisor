"""Native advising tools preserve grounding, scope, and deterministic ordering."""

from copy import deepcopy
from uuid import uuid4

import pytest

from app.atlas.requisites import AllOf, AnyOf, CourseRef
from app.config import load_settings
from app.generation.answer import answer_from_context
from app.generation.answer_tools import answer_tool_context
from app.llm.base import GenerationResult
from app.retrieval.types import CitedChunk
from app.tools.registry import ToolInputError, ToolPermissionDenied


def _fixture():
    source_id = uuid4()
    chunks = [CitedChunk(
        chunk_id=uuid4(), source_id=source_id, chunk_index=index,
        source_name="atlas.jsonl", locator_label=code, text=f"Evidence for {code}",
        seams=("keyword",), normalized_score=1.0, source_slot=1, rank=index + 1,
    ) for index, code in enumerate(("MATH 111", "MATH 112", "MATH 211"))]
    courses = [{
        "code": code, "title": code, "citation_numbers": (index + 1,),
        "parse_status": "none" if index == 0 else "parsed",
        "prerequisites": None if index == 0 else CourseRef("MATH 111"),
        "corequisites": None, "requisites_raw": None if index == 0 else "MATH 111",
        "requirements_evidence_complete": True, "blocker_reason": None,
    } for index, code in enumerate(("MATH 111", "MATH 112", "MATH 211"))]
    context = {"courses": courses, "topic_groups": [{
        "cluster_id": uuid4(), "path": ("Mathematics", "Calculus"),
        "total_size": 4, "course_codes": tuple(course["code"] for course in courses),
    }]}
    return chunks, context


def _tools(chunks, context):
    return answer_tool_context(query="calculus sequence", chunks=chunks,
                               workspace_key="development", settings=load_settings(),
                               advising_context=context)


def test_topics_distinguish_full_group_size_from_only_supported_members():
    chunks, context = _fixture()
    registry, scope, _ = _tools(chunks, context)
    result = registry.invoke("advisor_explore_topics", {
        "resource_ids": [str(chunk.chunk_id) for chunk in chunks[:2]],
    }, scope)
    group = result.groups[0]
    assert group.total_courses == 4 and group.evidence_complete is False
    assert [course.code for course in group.supported_courses] == ["MATH 111", "MATH 112"]
    assert [course.citation_numbers for course in group.supported_courses] == [[1], [2]]
    with pytest.raises(ToolPermissionDenied):
        registry.invoke("advisor_explore_topics", {"resource_ids": [str(uuid4())]}, scope)


def test_sequence_uses_evidence_and_discloses_completed_course_assumptions():
    chunks, context = _fixture()
    registry, scope, _ = _tools(chunks, context)
    resources = [str(chunk.chunk_id) for chunk in chunks]
    args = {"resource_ids": resources, "selected_codes": ["math111", "MATH 112", "MATH 211"]}
    result = registry.invoke("advisor_plan_course_sequence", args, scope)
    assert result.stages == [["MATH 111"], ["MATH 112", "MATH 211"]]
    assert result.complete and result.scope == "prerequisite_order_only"
    assert result.enrollment_eligibility_verified is False
    conditional = registry.invoke("advisor_plan_course_sequence", {
        **args, "completed_codes": ["MATH 111"],
    }, scope)
    assert conditional.stages == [["MATH 112", "MATH 211"]]
    assert conditional.assumed_completed_codes == ["MATH 111"]
    with pytest.raises(ToolInputError):
        registry.invoke("advisor_plan_course_sequence", {**args, "selected_codes": ["nonsense"]}, scope)


def test_unresolved_or_is_never_chosen_and_missing_course_is_an_evidence_gap():
    chunks, context = _fixture()
    context["courses"][2]["prerequisites"] = AnyOf((CourseRef("MATH 111"), CourseRef("MATH 112")))
    context["courses"][2]["requisites_raw"] = "MATH 111 or MATH 112"
    registry, scope, _ = _tools(chunks, context)
    result = registry.invoke("advisor_plan_course_sequence", {
        "resource_ids": [str(chunk.chunk_id) for chunk in chunks],
        "selected_codes": ["MATH 211", "MATH 999"],
    }, scope)
    assert not result.complete and result.stages == []
    assert {(item.code, item.reason) for item in result.blockers} == {
        ("MATH 211", "alternative_required"), ("MATH 999", "course_evidence_missing"),
    }


def test_missing_requisite_evidence_blocks_sequence_and_narrow_scope_cannot_inherit_it():
    chunks, context = _fixture()
    course = context["courses"][1]
    course.update(parse_status="unparsed", prerequisites=None, requisites_raw=None,
                  requirements_evidence_complete=False, blocker_reason="requirements_evidence_missing")
    registry, scope, _ = _tools(chunks, context)
    result = registry.invoke("advisor_plan_course_sequence", {
        "resource_ids": [str(chunk.chunk_id) for chunk in chunks], "selected_codes": ["MATH 112"],
    }, scope)
    assert result.stages == [] and not result.complete
    assert result.blockers[0].reason == "requirements_evidence_missing"

    context["courses"][2]["citation_numbers"] = (2, 3)
    registry, scope, _ = _tools(chunks, context)
    result = registry.invoke("advisor_read_course_requirements", {
        "resource_ids": [str(chunks[2].chunk_id)], "course_codes": ["MATH 211"],
    }, scope)
    assert result.courses == [] and result.blockers[0].reason == "course_evidence_missing"


def test_replay_identity_changes_when_graph_or_boolean_requirements_change():
    chunks, context = _fixture()
    _, _, fingerprint = _tools(chunks, context)
    modified = deepcopy(context)
    modified["topic_groups"][0]["cluster_id"] = uuid4()
    assert _tools(chunks, modified)[2] != fingerprint
    modified = deepcopy(context)
    modified["courses"][2]["prerequisites"] = AllOf((CourseRef("MATH 111"), CourseRef("MATH 112")))
    and_fingerprint = _tools(chunks, modified)[2]
    modified["courses"][2]["prerequisites"] = AnyOf((CourseRef("MATH 111"), CourseRef("MATH 112")))
    assert _tools(chunks, modified)[2] != and_fingerprint


def test_resource_ids_are_in_the_tool_prompt_and_citations_still_use_chunk_positions():
    chunks, _ = _fixture()

    class Provider:
        def generate(self, request):
            for chunk in chunks:
                assert f"resource_id={chunk.chunk_id}" in request.messages[1]["content"]
            return GenerationResult(
                text="The second course is MATH 112 [2].", finish_reason="stop"
            )

    answer = answer_from_context(chunks, "calculus", provider=Provider(), include_resource_ids=True)
    assert answer.citations[0].chunk_id == chunks[1].chunk_id
