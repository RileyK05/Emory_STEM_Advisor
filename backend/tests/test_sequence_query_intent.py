"""Course sequencing must not capture biological sequence topic lookups."""

import pytest

from app.api.query_service import _requests_course_sequence


@pytest.mark.parametrize("query", [
    "Use the deterministic sequence tool to order ECON 101 and ECON 112.",
    "What is the next dependency stage for ECON 112?",
    "What course sequence makes sense here?",
    "What sequence should I take?",
    "What sequence to take it in?",
])
def test_explicit_course_ordering_requires_planner(query):
    assert _requests_course_sequence(query)


@pytest.mark.parametrize("query", [
    "Which courses teach DNA sequence analysis?",
    "Does BIOL 441 cover RNA sequence analysis?",
    "I want a career in macroeconomics. Which courses should I explore?",
])
def test_topic_queries_do_not_force_prerequisite_tools(query):
    assert not _requests_course_sequence(query)
