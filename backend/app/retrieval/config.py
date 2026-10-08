"""Versioned retrieval config. Unknown keys are errors, not defaults."""

import tomllib
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from app.config import Settings, load_settings

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "retrieval.toml"


class ConfigError(ValueError):
    """A malformed or out-of-budget retrieval config."""


@dataclass(frozen=True)
class ChunkingConfig:
    chunker_version: str
    target_tokens: int
    overlap_tokens: int
    text_lines_per_locator: int


@dataclass(frozen=True)
class KeywordSeamConfig:
    enabled: bool
    limit: int
    min_token_len: int


@dataclass(frozen=True)
class EmbedSeamConfig:
    enabled: bool
    limit: int
    only_quota: int
    # Minimum cosine similarity between the query and a chunk. Below this a
    # chunk is unrelated noise; it is dropped rather than fused. Vectors are
    # unit-norm, so this is a dot product in [-1, 1].
    min_similarity: float


@dataclass(frozen=True)
class PrereqSeamConfig:
    enabled: bool
    decay: float
    directions: tuple[str, ...]
    limit: int


@dataclass(frozen=True)
class SeamsConfig:
    keyword: KeywordSeamConfig
    embed: EmbedSeamConfig
    prereq: PrereqSeamConfig


@dataclass(frozen=True)
class FusionConfig:
    cited_set_size: int
    # Fused chunks scoring below this are dropped before citation; if none
    # remain the query is a refusal. Prevents unrelated material from reaching
    # the model (a prior-system failure mode). Keyword min-max means the best
    # keyword chunk is always 1.0, so an unrelated keyword-only query still
    # yields one candidate at the top of its range.
    min_score: float
    # Collapse cited chunks with identical text into one citation, keeping the
    # highest-scoring instance.
    dedupe: bool


@dataclass(frozen=True)
class SimgraphConfig:
    edge_floor: float
    # Cosine-distance cuts of the dendrogram, one per level below the root.
    # Strictly decreasing, so each level nests inside the one above.
    level_cut_distances: tuple[float, ...]
    linkage: str
    block_size: int
    walk_top_margin: float
    walk_min_similarity: float
    walk_min_subtree: int


@dataclass(frozen=True)
class RetrievalConfig:
    version: int
    chunking: ChunkingConfig
    seams: SeamsConfig
    fusion: FusionConfig
    simgraph: SimgraphConfig


def _require(section: dict, key: str, where: str):
    if key not in section:
        raise ConfigError(f"missing key {key!r} in [{where}]")
    return section[key]


def _reject_unknown(section: dict, allowed: set[str], where: str) -> None:
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"unknown keys in [{where}]: {sorted(unknown)}")


def _check_budget(chunking: ChunkingConfig, cited_set_size: int, settings: Settings) -> None:
    if cited_set_size * chunking.target_tokens > 0.6 * settings.hf_max_context:
        raise ConfigError(
            f"cited_set_size ({cited_set_size}) * target_tokens "
            f"({chunking.target_tokens}) exceeds 60% of HF_MAX_CONTEXT "
            f"({settings.hf_max_context})"
        )


def load_retrieval_config(
    path: Path | None = None, settings: Settings | None = None
) -> RetrievalConfig:
    path = path or _CONFIG_PATH
    settings = settings or load_settings()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    _reject_unknown(
        raw, {"version", "chunking", "seams", "fusion", "simgraph"}, "root"
    )
    version = _require(raw, "version", "root")

    chunking_raw = _require(raw, "chunking", "root")
    _reject_unknown(
        chunking_raw,
        {"chunker_version", "target_tokens", "overlap_tokens", "text_lines_per_locator"},
        "chunking",
    )
    chunking = ChunkingConfig(
        chunker_version=str(_require(chunking_raw, "chunker_version", "chunking")),
        target_tokens=int(_require(chunking_raw, "target_tokens", "chunking")),
        overlap_tokens=int(_require(chunking_raw, "overlap_tokens", "chunking")),
        text_lines_per_locator=int(
            _require(chunking_raw, "text_lines_per_locator", "chunking")
        ),
    )
    if chunking.target_tokens <= 0:
        raise ConfigError("target_tokens must be positive")
    if chunking.overlap_tokens < 0 or chunking.overlap_tokens >= chunking.target_tokens:
        raise ConfigError("overlap_tokens must be in [0, target_tokens)")
    if chunking.text_lines_per_locator <= 0:
        raise ConfigError("text_lines_per_locator must be positive")

    seams_raw = _require(raw, "seams", "root")
    _reject_unknown(seams_raw, {"keyword", "embed", "prereq"}, "seams")
    keyword_raw = _require(seams_raw, "keyword", "seams")
    _reject_unknown(keyword_raw, {"enabled", "limit", "min_token_len"}, "seams.keyword")
    keyword = KeywordSeamConfig(
        enabled=bool(_require(keyword_raw, "enabled", "seams.keyword")),
        limit=int(_require(keyword_raw, "limit", "seams.keyword")),
        min_token_len=int(_require(keyword_raw, "min_token_len", "seams.keyword")),
    )
    if keyword.limit <= 0:
        raise ConfigError("seams.keyword.limit must be positive")
    if keyword.min_token_len < 1:
        raise ConfigError("seams.keyword.min_token_len must be >= 1")

    embed_raw = _require(seams_raw, "embed", "seams")
    _reject_unknown(embed_raw, {"enabled", "limit", "only_quota", "min_similarity"}, "seams.embed")
    embed = EmbedSeamConfig(
        enabled=bool(_require(embed_raw, "enabled", "seams.embed")),
        limit=int(_require(embed_raw, "limit", "seams.embed")),
        only_quota=int(_require(embed_raw, "only_quota", "seams.embed")),
        min_similarity=float(_require(embed_raw, "min_similarity", "seams.embed")),
    )
    if embed.limit <= 0:
        raise ConfigError("seams.embed.limit must be positive")
    if embed.only_quota < 0:
        raise ConfigError("seams.embed.only_quota must be >= 0")
    if not -1.0 <= embed.min_similarity <= 1.0:
        raise ConfigError("seams.embed.min_similarity must be in [-1, 1]")

    prereq_raw = _require(seams_raw, "prereq", "seams")
    _reject_unknown(
        prereq_raw, {"enabled", "decay", "directions", "limit"}, "seams.prereq"
    )
    prereq = PrereqSeamConfig(
        enabled=bool(_require(prereq_raw, "enabled", "seams.prereq")),
        decay=float(_require(prereq_raw, "decay", "seams.prereq")),
        directions=tuple(
            str(value) for value in _require(prereq_raw, "directions", "seams.prereq")
        ),
        limit=int(_require(prereq_raw, "limit", "seams.prereq")),
    )
    allowed_directions = {"self", "upstream", "downstream"}
    bad = set(prereq.directions) - allowed_directions
    if bad:
        raise ConfigError(f"unknown prereq directions: {sorted(bad)}")
    if not prereq.directions:
        raise ConfigError("seams.prereq.directions must not be empty")
    if prereq.limit <= 0:
        raise ConfigError("seams.prereq.limit must be positive")

    fusion_raw = _require(raw, "fusion", "root")
    _reject_unknown(fusion_raw, {"cited_set_size", "min_score", "dedupe"}, "fusion")
    fusion = FusionConfig(
        cited_set_size=int(_require(fusion_raw, "cited_set_size", "fusion")),
        min_score=float(_require(fusion_raw, "min_score", "fusion")),
        dedupe=bool(_require(fusion_raw, "dedupe", "fusion")),
    )
    if fusion.cited_set_size <= 0:
        raise ConfigError("fusion.cited_set_size must be positive")
    if not 0.0 <= fusion.min_score <= 1.0:
        raise ConfigError("fusion.min_score must be in [0, 1]")

    simgraph_raw = _require(raw, "simgraph", "root")
    _reject_unknown(
        simgraph_raw,
        {
            "edge_floor",
            "level_cut_distances",
            "linkage",
            "block_size",
            "walk_top_margin",
            "walk_min_similarity",
            "walk_min_subtree",
        },
        "simgraph",
    )
    simgraph = SimgraphConfig(
        edge_floor=float(_require(simgraph_raw, "edge_floor", "simgraph")),
        level_cut_distances=tuple(
            float(value)
            for value in _require(simgraph_raw, "level_cut_distances", "simgraph")
        ),
        linkage=str(_require(simgraph_raw, "linkage", "simgraph")),
        block_size=int(_require(simgraph_raw, "block_size", "simgraph")),
        walk_top_margin=float(
            _require(simgraph_raw, "walk_top_margin", "simgraph")
        ),
        walk_min_similarity=float(
            _require(simgraph_raw, "walk_min_similarity", "simgraph")
        ),
        walk_min_subtree=int(_require(simgraph_raw, "walk_min_subtree", "simgraph")),
    )
    if not -1.0 <= simgraph.edge_floor <= 1.0:
        raise ConfigError("simgraph.edge_floor must be in [-1, 1]")
    cuts = simgraph.level_cut_distances
    if not cuts:
        raise ConfigError("simgraph.level_cut_distances must not be empty")
    if any(not 0.0 < cut <= 2.0 for cut in cuts):
        raise ConfigError("simgraph.level_cut_distances must be in (0, 2]")
    if any(later >= earlier for earlier, later in pairwise(cuts)):
        raise ConfigError("simgraph.level_cut_distances must be strictly decreasing")
    # Only methods that are valid on cosine distances and monotone (so the
    # cuts nest). ward/centroid/median assume Euclidean distances.
    if simgraph.linkage not in {"average", "complete", "single", "weighted"}:
        raise ConfigError(
            "simgraph.linkage must be one of average, complete, single, weighted"
        )
    if simgraph.block_size <= 0:
        raise ConfigError("simgraph.block_size must be positive")
    if simgraph.walk_min_subtree < 0:
        raise ConfigError("simgraph.walk_min_subtree must be >= 0")

    _check_budget(chunking, fusion.cited_set_size, settings)
    return RetrievalConfig(
        version=int(version),
        chunking=chunking,
        seams=SeamsConfig(keyword=keyword, embed=embed, prereq=prereq),
        fusion=fusion,
        simgraph=simgraph,
    )
