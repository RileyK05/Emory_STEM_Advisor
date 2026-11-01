"""Guards for advisor_compare_mentions input and page shaping."""

from types import SimpleNamespace
from uuid import uuid4

from app.advising.comparison import MENTION_OVERLAP_ONLY, MentionComparison, MentionRef
from app.generation.research_tools import CompareInput, ResearchSession


def test_compare_rejects_duplicate_program_handles():
    session = ResearchSession.__new__(ResearchSession)
    session.program_reads = {"h1": object()}
    result = ResearchSession.compare(
        session, CompareInput(handles=["h1", "h1"]), _scope=None
    )
    assert result.status == "conflict"
    assert "distinct" in result.limitations[0].lower()


def test_compare_keeps_all_mention_refs_for_retained_codes():
    pid_a, pid_b = uuid4(), uuid4()
    refs = tuple(
        MentionRef(program_id=pid_a, position=i, text=f"line {i}", choose=None, cited=True)
        for i in range(6)
    )
    comparison = MentionComparison(
        kind=MENTION_OVERLAP_ONLY,
        programs=(pid_a, pid_b),
        shared={"CS 170": refs},
        unique={pid_a: {}, pid_b: {"MATH 111": refs[:3]}},
        unresolved_positions={},
    )

    session = ResearchSession.__new__(ResearchSession)
    session.program_reads = {"h1": object(), "h2": object()}
    session.program_positions = {"h1": [0, 1], "h2": [0, 1]}
    session.scope = object()
    session.conn = SimpleNamespace(commit=lambda: None)
    session.resolve = lambda handle, kind: object()

    import app.generation.research_tools as mod

    original = mod.compare_mentions
    original_read = mod.read_program
    mod.compare_mentions = lambda programs: comparison
    mod.read_program = lambda *args, **kwargs: SimpleNamespace(items=[0, 1])
    try:
        result = ResearchSession.compare(
            session, CompareInput(handles=["h1", "h2"]), _scope=None
        )
    finally:
        mod.compare_mentions = original
        mod.read_program = original_read

    assert result.status == "ok"
    shared_refs = result.data["comparison"]["shared"]["CS 170"]
    assert len(shared_refs) == 6
