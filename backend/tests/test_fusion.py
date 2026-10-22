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


def test_soft_source_cap_admits_a_stronger_sibling():
    strong, medium, weak = uuid4(), uuid4(), uuid4()
    keyword = _seam(
        "keyword",
        [
            _candidate(strong, 0, "keyword", 10.0),
            _candidate(strong, 1, "keyword", 8.0),
            _candidate(medium, 0, "keyword", 5.0),
            _candidate(weak, 0, "keyword", 3.0),
        ],
    )

    fused = fuse([keyword], cited_set_size=2, embed_only_quota=4)

    assert [(c.source_id, c.chunk_index) for c in fused] == [
        (strong, 0), (strong, 1)
    ]
    assert medium not in {c.source_id for c in fused}
    assert weak not in {c.source_id for c in fused}


def test_full_source_budget_keeps_every_covered_source():
    strong, medium, weak = uuid4(), uuid4(), uuid4()
    keyword = _seam(
        "keyword",
        [
            _candidate(strong, 0, "keyword", 10.0),
            _candidate(strong, 1, "keyword", 8.0),
            _candidate(medium, 0, "keyword", 5.0),
            _candidate(weak, 0, "keyword", 3.0),
        ],
    )

    fused = fuse([keyword], cited_set_size=3, embed_only_quota=4)

    assert {c.source_id for c in fused} == {strong, medium, weak}
    assert (strong, 1) not in {(c.source_id, c.chunk_index) for c in fused}


def test_soft_source_cap_keeps_diversity_when_sibling_is_weaker():
    strong, medium, weak = uuid4(), uuid4(), uuid4()
    keyword = _seam(
        "keyword",
        [
            _candidate(strong, 0, "keyword", 10.0),
            _candidate(strong, 1, "keyword", 2.0),
            _candidate(medium, 0, "keyword", 8.0),
            _candidate(weak, 0, "keyword", 7.0),
        ],
    )

    fused = fuse([keyword], cited_set_size=2, embed_only_quota=4)

    assert {c.source_id for c in fused} == {strong, medium}
    assert all(c.chunk_index == 0 for c in fused)


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


def test_weak_lexical_matches_are_not_promoted_to_the_top():
    # DA-03: a weak keyword set must not be stretched to a perfect 1.0.
    # The configured relevance floor then drops it, so boilerplate cannot
    # occupy the top cited slot or count as a grounded hit.
    source = uuid4()
    seam = _seam(
        "keyword",
        [
            _candidate(source, 0, "keyword", 0.02),
            _candidate(source, 1, "keyword", 0.01),
        ],
    )
    kept = fuse([seam], cited_set_size=6, embed_only_quota=4)
    assert [c.normalized_score for c in kept] == [0.02, 0.01]
    assert fuse([seam], cited_set_size=6, embed_only_quota=4, min_score=0.15) == []
    topical = _candidate(uuid4(), 0, "keyword", 5.2)
    generic = _candidate(uuid4(), 0, "keyword", 0.2)
    ranked = fuse(
        [_seam("keyword", [generic, topical])],
        cited_set_size=6, embed_only_quota=4, min_score=0.15,
    )
    assert [c.chunk_id for c in ranked] == [topical.chunk_id]


def test_duplicate_candidates_do_not_consume_context_slots():
    sources = [uuid4() for _ in range(4)]
    candidates = [_candidate(source, 0, "keyword", 10 - i)
                  for i, source in enumerate(sources)]
    texts = {c.chunk_id: text for c, text in zip(
        candidates, ["same passage", "same passage\n", "distinct evidence", "weak"],
        strict=True,
    )}
    fused = fuse([_seam("keyword", candidates)], cited_set_size=2, embed_only_quota=4,
                 chunk_texts=texts)
    assert [texts[c.chunk_id].strip() for c in fused] == ["same passage", "distinct evidence"]


def test_duplicate_embeddings_do_not_consume_embedding_quota():
    grounded = _candidate(uuid4(), 0, "prereq", 1.0)
    candidates = [_candidate(uuid4(), 0, "embed", score) for score in (0.9, 0.8, 0.7, 0.3)]
    texts = {c.chunk_id: text for c, text in zip(
        [grounded, *candidates], ["grounded", "same", "same", "different", "weak"],
        strict=True,
    )}
    fused = fuse([_seam("prereq", [grounded]), _seam("embed", candidates)],
                 cited_set_size=3, embed_only_quota=2, chunk_texts=texts)
    assert {texts[c.chunk_id] for c in fused} == {"grounded", "same", "different"}


def test_cross_listed_aliases_do_not_consume_context_slots():
    # Two official listings of one course have different headers, so text
    # dedupe misses them; the canonical key collapses them to one before any
    # quota, while an unrelated course is kept.
    source = uuid4()
    alias_a = _candidate(source, 0, "keyword", 10.0)
    alias_b = _candidate(source, 1, "keyword", 9.0)
    distinct = _candidate(source, 2, "keyword", 8.0)
    texts = {
        alias_a.chunk_id: "AAS 325: Black Love\n\nSame body.",
        alias_b.chunk_id: "REL 325: Black Love\n\nSame body.",
        distinct.chunk_id: "MATH 112: Calculus II\n\nSeries and limits.",
    }
    fused = fuse(
        [_seam("keyword", [alias_a, alias_b, distinct])],
        cited_set_size=6, embed_only_quota=4, chunk_texts=texts,
        canonical_keys={
            alias_a.chunk_id: "AAS 325",
            alias_b.chunk_id: "AAS 325",
            distinct.chunk_id: "MATH 112",
        },
    )
    assert [c.chunk_id for c in fused] == [alias_a.chunk_id, distinct.chunk_id]


def test_filtered_grounded_hit_does_not_limit_surviving_embeddings():
    prerequisite = _candidate(uuid4(), 0, "prereq", 0.1)
    embedding = [_candidate(uuid4(), 0, "embed", score) for score in (0.9, 0.8, 0.7, 0.1)]
    fused = fuse([_seam("prereq", [prerequisite]), _seam("embed", embedding)],
                 cited_set_size=3, embed_only_quota=1, min_score=0.5)
    assert len(fused) == 3
    assert all(c.seams == ("embed",) for c in fused)


def test_filtered_source_representative_does_not_leave_a_context_hole():
    strong, weak = uuid4(), uuid4()
    candidates = [_candidate(strong, i, "prereq", score)
                  for i, score in enumerate((1.0, 0.8, 0.6))]
    candidates.append(_candidate(weak, 0, "prereq", 0.1))
    fused = fuse([_seam("prereq", candidates)], cited_set_size=3,
                 embed_only_quota=4, min_score=0.5)
    assert [(c.source_id, c.chunk_index) for c in fused] == [
        (strong, 0), (strong, 1), (strong, 2),
    ]


def test_dedupe_keeps_real_grounded_identity_on_a_score_tie():
    grounded = _candidate(uuid4(), 0, "keyword", 1.0)
    embedded = _candidate(uuid4(), 0, "embed", 0.8)
    fused = fuse([_seam("keyword", [grounded]), _seam("embed", [embedded])],
                 cited_set_size=2, embed_only_quota=0,
                 chunk_texts={grounded.chunk_id: "shared", embedded.chunk_id: "shared"})
    assert len(fused) == 1
    assert fused[0].chunk_id == grounded.chunk_id
    assert fused[0].seams == ("keyword",)
