"""Fusion: normalize, union, quota, allocate across sources. Pure, no DB.

Every seam is normalized higher-is-better. Multiplicity is recorded but never
added. A grounded seam's hits bound embedding-only hits via the quota.

Most seams are min-max normalized per query. The prerequisite seam is not: its
scores are fixed config values in (0, 1] (1.0 for the asked-about course,
`decay` for a prerequisite), and min-max would turn every prerequisite into 0.
"""

from dataclasses import dataclass
from uuid import UUID

from app.retrieval.types import Candidate, SeamResult

GROUNDED_SEAMS = frozenset({"keyword", "prereq"})
# Seams whose raw scores are already on the shared [0, 1] scale.
FIXED_SCALE_SEAMS = frozenset({"prereq"})


@dataclass(frozen=True)
class FusedChunk:
    chunk_id: UUID
    source_id: UUID
    chunk_index: int
    seams: tuple[str, ...]
    normalized_score: float
    source_slot: int
    rank: int


def _normalize(name: str, candidates: list[Candidate]) -> dict[UUID, float]:
    if not candidates:
        return {}
    if name in FIXED_SCALE_SEAMS:
        return {c.chunk_id: min(1.0, max(0.0, c.raw_score)) for c in candidates}
    scores = [c.raw_score for c in candidates]
    low, high = min(scores), max(scores)
    if high == low:
        return {c.chunk_id: 1.0 for c in candidates}
    return {c.chunk_id: (c.raw_score - low) / (high - low) for c in candidates}


def _candidate_key(chunk_id: UUID, scores: dict[UUID, float], meta: dict[UUID, Candidate]):
    candidate = meta[chunk_id]
    return (-scores[chunk_id], str(candidate.source_id), candidate.chunk_index, str(chunk_id))


def _select_with_soft_source_cap(
    by_source: dict[UUID, list[UUID]],
    scores: dict[UUID, float],
    meta: dict[UUID, Candidate],
    budget: int,
    source_order: list[UUID],
) -> list[UUID]:
    """Prefer one passage per source, then spend slots on stronger siblings.

    The first pass preserves source diversity by choosing each source's best
    passage in source-score order. If the budget is smaller than the number of
    sources, a sibling can displace the weakest selected source only when its
    score is strictly higher than that source's representative. When the
    budget can cover every source, remaining slots go to the strongest
    remaining passages globally.
    """
    if budget <= 0 or not source_order:
        return []

    ranked = {
        source: sorted(chunk_ids, key=lambda cid: _candidate_key(cid, scores, meta))
        for source, chunk_ids in by_source.items()
    }
    represented = source_order[: min(budget, len(source_order))]
    selected = [ranked[source][0] for source in represented]

    if budget > len(source_order):
        remaining = [
            chunk_id
            for source in source_order
            for chunk_id in ranked[source][1:]
        ]
        remaining.sort(key=lambda cid: _candidate_key(cid, scores, meta))
        selected.extend(remaining[: budget - len(selected)])
        return selected

    # Under a tight budget, do not let a merely available sibling crowd out a
    # distinct source. It must outrank the weakest selected representative.
    alternatives = [
        chunk_id
        for source in represented
        for chunk_id in ranked[source][1:]
    ]
    alternatives.sort(key=lambda cid: _candidate_key(cid, scores, meta))
    for candidate_id in alternatives:
        represented_sources = {meta[chunk_id].source_id for chunk_id in selected}
        if meta[candidate_id].source_id not in represented_sources:
            continue
        weakest_id = max(selected, key=lambda cid: _candidate_key(cid, scores, meta))
        if scores[candidate_id] <= scores[weakest_id]:
            break
        selected.remove(weakest_id)
        selected.append(candidate_id)
    return selected


def fuse(
    results: list[SeamResult],
    cited_set_size: int,
    embed_only_quota: int,
    *,
    min_score: float = 0.0,
    chunk_texts: dict[UUID, str] | None = None,
    canonical_keys: dict[UUID, str] | None = None,
) -> list[FusedChunk]:
    normalized: dict[UUID, float] = {}
    provenance: dict[UUID, set[str]] = {}
    meta: dict[UUID, Candidate] = {}

    for result in results:
        scores = _normalize(result.name, result.candidates)
        for candidate in result.candidates:
            chunk_id = candidate.chunk_id
            score = scores[chunk_id]
            if chunk_id not in normalized or score > normalized[chunk_id]:
                normalized[chunk_id] = score
            provenance.setdefault(chunk_id, set()).add(result.name)
            meta.setdefault(chunk_id, candidate)

    # Filter before allocating slots. Otherwise a source's weak representative
    # can consume a slot that is discarded after selection, burying a stronger
    # sibling from another source.
    for chunk_id in list(normalized):
        if normalized[chunk_id] < min_score:
            del normalized[chunk_id]
            del provenance[chunk_id]
            del meta[chunk_id]

    # Deduplicate before the embedding quota and final context budget. Repeated
    # passages must not consume either budget and then leave holes at citation.
    # Keep the strongest actual chunk identity; do not transfer another chunk's
    # seam provenance or invent a merged citation. A canonical key collapses
    # officially cross-listed aliases to one underlying course, again before the
    # quota, so an alias cannot displace a distinct course.
    if chunk_texts is not None or canonical_keys is not None:
        seen_text: set[str] = set()
        seen_canonical: set[str] = set()
        for chunk_id in sorted(normalized, key=lambda cid: (
            -normalized[cid],
            not bool(provenance[cid] & GROUNDED_SEAMS),
            str(meta[cid].source_id), meta[cid].chunk_index, str(cid),
        )):
            text = chunk_texts.get(chunk_id) if chunk_texts is not None else None
            if chunk_texts is not None and text is None:
                continue  # The citation contract check will reject a missing chunk.
            fingerprint = text.strip() if text is not None else None
            canonical = (canonical_keys or {}).get(chunk_id)
            duplicate = (
                (fingerprint is not None and fingerprint in seen_text)
                or (canonical is not None and canonical in seen_canonical)
            )
            if duplicate:
                del normalized[chunk_id]
                del provenance[chunk_id]
                del meta[chunk_id]
            else:
                if fingerprint is not None:
                    seen_text.add(fingerprint)
                if canonical is not None:
                    seen_canonical.add(canonical)

    grounded_present = any(seams & GROUNDED_SEAMS for seams in provenance.values())
    if grounded_present:
        embed_only = sorted(
            [cid for cid, seams in provenance.items() if seams == {"embed"}],
            key=lambda cid: (-normalized[cid], str(meta[cid].source_id),
                             meta[cid].chunk_index),
        )
        for chunk_id in embed_only[embed_only_quota:]:
            del normalized[chunk_id]
            del provenance[chunk_id]
            del meta[chunk_id]

    by_source: dict[UUID, list[UUID]] = {}
    for chunk_id in normalized:
        by_source.setdefault(meta[chunk_id].source_id, []).append(chunk_id)

    weights = {
        source: max(normalized[cid] for cid in chunk_ids)
        for source, chunk_ids in by_source.items()
    }
    order = sorted(by_source, key=lambda s: (-weights[s], str(s)))
    source_slot = {source: index + 1 for index, source in enumerate(order)}

    selected = _select_with_soft_source_cap(
        by_source, normalized, meta, cited_set_size, order
    )

    selected.sort(
        key=lambda cid: (
            -normalized[cid],
            str(meta[cid].source_id),
            meta[cid].chunk_index,
        )
    )
    return [
        FusedChunk(
            chunk_id=cid,
            source_id=meta[cid].source_id,
            chunk_index=meta[cid].chunk_index,
            seams=tuple(sorted(provenance[cid])),
            normalized_score=normalized[cid],
            source_slot=source_slot[meta[cid].source_id],
            rank=index + 1,
        )
        for index, cid in enumerate(selected)
    ]
