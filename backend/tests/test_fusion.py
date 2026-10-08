"""Pure fusion tests: normalization, max-not-sum, quota, allocation."""

from uuid import NAMESPACE_URL, uuid4, uuid5

from app.retrieval.fusion import fuse
from app.retrieval.types import Candidate, SeamResult


def _chunk_id(source_id, chunk_index):
    return uuid5(NAMESPACE_URL, f"{source_id}:{chunk_index}")


def _candidate(source_id, chunk_index, seam, score):
    return Candidate(
        chunk_id=_chunk_id(source_id, chunk_index),
        source_id=source_id,
        chunk_index=chunk_index,
        seam=seam,
        raw_score=score,
    )


def _seam(name, candidates):
    return SeamResult(name=name, enabled=True, state="active", candidates=candidates)


def test_normalization_is_higher_is_better():
    source = uuid4()
    result = _seam("keyword", [
        _candidate(source, 0, "keyword", 10.0),
        _candidate(source, 1, "keyword", 5.0),
    ])
    fused = fuse([result], cited_set_size=6, embed_only_quota=4)
    scores = {c.chunk_index: c.normalized_score for c in fused}
    assert scores[0] == 1.0
    assert scores[1] == 0.0


def test_max_not_sum_across_seams():
    source = uuid4()
    a = _candidate(source, 0, "keyword", 10.0)
    b = _candidate(source, 1, "keyword", 1.0)
    shared_id = _chunk_id(source, 2)
    keyword = _seam("keyword", [
        a,
        b,
        Candidate(shared_id, source, 2, "keyword", 5.0),
    ])
    prereq = _seam("prereq", [Candidate(shared_id, source, 2, "prereq", 0.8)])
    fused = fuse([keyword, prereq], cited_set_size=6, embed_only_quota=4)
    shared = next(c for c in fused if c.chunk_id == shared_id)
    assert shared.seams == ("keyword", "prereq")
    # keyword's 5.0 normalizes to 0.444; prereq keeps its fixed 0.8; max wins.
    assert shared.normalized_score == 0.8


def test_embedding_quota_applies_when_grounded_present():
    source = uuid4()
    keyword = _seam("keyword", [_candidate(source, 0, "keyword", 5.0)])
    embed = _seam(
        "embed",
        [_candidate(source, i, "embed", float(10 - i)) for i in range(1, 6)],
    )
    fused = fuse([keyword, embed], cited_set_size=20, embed_only_quota=2)
    embed_only = [c for c in fused if c.seams == ("embed",)]
    assert len(embed_only) == 2


def test_no_grounded_seam_means_no_quota():
    source = uuid4()
    embed = _seam(
        "embed",
        [_candidate(source, i, "embed", float(10 - i)) for i in range(6)],
    )
    fused = fuse([embed], cited_set_size=20, embed_only_quota=2)
    assert len(fused) == 6


def test_allocation_floor_and_diversity():
    # Two sources: one high-relevance, one low. Both must get at least one slot.
    strong, weak = uuid4(), uuid4()
    keyword = _seam(
        "keyword",
        [
            _candidate(strong, 0, "keyword", 10.0),
            _candidate(strong, 1, "keyword", 9.0),
            _candidate(strong, 2, "keyword", 8.0),
            _candidate(weak, 0, "keyword", 1.0),
        ],
    )
    fused = fuse([keyword], cited_set_size=3, embed_only_quota=4)
    sources = {c.source_id for c in fused}
    assert sources == {strong, weak}
    assert sum(1 for c in fused if c.source_id == weak) == 1


def test_availability_clamp_redistributes():
    strong, weak = uuid4(), uuid4()
    keyword = _seam(
        "keyword",
        [
            _candidate(strong, i, "keyword", float(10 - i)) for i in range(5)
        ]
        + [_candidate(weak, 0, "keyword", 0.5)],
    )
    fused = fuse([keyword], cited_set_size=6, embed_only_quota=4)
    assert len(fused) == 6


def test_empty_results_refuse():
    assert fuse([], cited_set_size=6, embed_only_quota=4) == []


def test_ties_break_deterministically():
    source = uuid4()
    seam = _seam(
        "keyword",
        [
            _candidate(source, 0, "keyword", 5.0),
            _candidate(source, 1, "keyword", 5.0),
            _candidate(source, 2, "keyword", 5.0),
        ],
    )
    first = fuse([seam], cited_set_size=6, embed_only_quota=4)
    second = fuse([seam], cited_set_size=6, embed_only_quota=4)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_prereq_scores_keep_their_fixed_scale():
    source = uuid4()
    asked = _candidate(source, 0, "prereq", 1.0)
    prerequisite = _candidate(source, 1, "prereq", 0.8)
    fused = fuse([_seam("prereq", [asked, prerequisite])], cited_set_size=6, embed_only_quota=4)
    scores = {c.chunk_index: c.normalized_score for c in fused}
    # Min-max would give the prerequisite 0.0 and bury it.
    assert scores == {0: 1.0, 1: 0.8}
