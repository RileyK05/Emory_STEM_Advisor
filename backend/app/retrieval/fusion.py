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


def _largest_remainder(weights: dict, total: int, order: list) -> dict:
    result = {source: 0 for source in order}
    if total <= 0 or not order:
        return result
    total_weight = sum(weights.values())
    if total_weight <= 0:
        for i, source in enumerate(order):
            result[source] = total // len(order) + (1 if i < total % len(order) else 0)
        return result
    raw = {source: total * weights[source] / total_weight for source in order}
    for source in order:
        result[source] = int(raw[source])
    remainder = total - sum(result.values())
    ranked = sorted(
        order, key=lambda s: (-(raw[s] - int(raw[s])), -weights[s], str(s))
    )
    for source in ranked[:remainder]:
        result[source] += 1
    return result


def _allocate(weights: dict, counts: dict, budget: int, order: list) -> dict:
    if not order:
        return {}
    if budget <= len(order):
        slots = {source: 0 for source in order}
        for source in order[:budget]:
            slots[source] = 1
        return slots

    slots = {source: 1 for source in order}  # one-slot floor
    remaining = budget - len(order)
    extra = _largest_remainder(weights, remaining, order)
    for source in order:
        slots[source] += extra[source]

    for source in order:
        slots[source] = min(slots[source], counts[source])

    capacity = {source: counts[source] - slots[source] for source in order}
    free = budget - sum(slots.values())
    # Redistribute freed slots by the same remainder order, then weight.
    while free > 0:
        available = [s for s in order if capacity[s] > 0]
        if not available:
            break
        source = min(available, key=lambda s: (-weights[s], str(s)))
        slots[source] += 1
        capacity[source] -= 1
        free -= 1
    return slots


def fuse(
    results: list[SeamResult],
    cited_set_size: int,
    embed_only_quota: int,
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

    grounded_present = any(
        result.candidates and result.name in GROUNDED_SEAMS for result in results
    )
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
    counts = {source: len(chunk_ids) for source, chunk_ids in by_source.items()}
    order = sorted(by_source, key=lambda s: (-weights[s], str(s)))
    source_slot = {source: index + 1 for index, source in enumerate(order)}

    slots = _allocate(weights, counts, cited_set_size, order)

    selected: list[UUID] = []
    for source in order:
        ranked = sorted(
            by_source[source],
            key=lambda cid: (-normalized[cid], meta[cid].chunk_index, str(cid)),
        )
        selected.extend(ranked[: slots[source]])

    if len(selected) < cited_set_size:
        remaining = sorted(
            [cid for cid in normalized if cid not in set(selected)],
            key=lambda cid: (-normalized[cid], str(meta[cid].source_id),
                             meta[cid].chunk_index),
        )
        selected.extend(remaining[: cited_set_size - len(selected)])

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
