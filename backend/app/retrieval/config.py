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
class PlanningConfig:
    # Bounds on a model-proposed search plan. The plan only chooses what to
    # look up; application code runs every search inside the query's scope.
    max_search_queries: int = 5
    max_course_codes: int = 12
    max_documents: int = 3
    # Context assembled from a plan: hits for the question itself, the best
    # passages inside planned documents, and per planned search query.
    question_slots: int = 4
    document_chunks: int = 2
    search_slots: int = 2
    context_size: int = 28


@dataclass(frozen=True)
class ResearchConfig:
    # Bounds on the research advising working state (docs/plans/research-advisor-v1.md).
    # These cap the model's own comparison, not the evidence it may read: the
    # durable register is separate and larger. Starting values, not a claim that
    # they are enough for every question. Kept in sync by hand with
    # app/advising/research_types.py, which owns the authoritative ceilings.
    max_candidates: int = 6
    max_evidence_items: int = 100
    max_evidence_bytes: int = 256_000
    max_state_bytes: int = 8_000
    # After this many rounds that add no evidence and no decision movement, the
    # run is offered a finish instead of paraphrasing more searches.
    no_progress_rounds: int = 2


@dataclass(frozen=True)
class SimgraphConfig:
    # Cosine-distance cuts within each fixed department, one per topic level.
    # Strictly decreasing, so each level nests inside the one above.
    level_cut_distances: tuple[float, ...]
    linkage: str
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
    planning: PlanningConfig = PlanningConfig()
    research: ResearchConfig = ResearchConfig()


def _require(section: dict, key: str, where: str):
    if key not in section:
        raise ConfigError(f"missing key {key!r} in [{where}]")
    return section[key]


def _reject_unknown(section: dict, allowed: set[str], where: str) -> None:
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"unknown keys in [{where}]: {sorted(unknown)}")


def _check_budget(chunking: ChunkingConfig, cited_set_size: int, settings: Settings) -> None:
    if chunking.target_tokens > settings.embedding_max_tokens:
        raise ConfigError("target_tokens exceeds EMBEDDING_MAX_TOKENS")
    if cited_set_size * chunking.target_tokens > 0.6 * settings.hf_max_context:
        raise ConfigError(
            f"cited_set_size ({cited_set_size}) * target_tokens "
            f"({chunking.target_tokens}) exceeds 60% of HF_MAX_CONTEXT "
            f"({settings.hf_max_context})"
        )


def _section(raw: dict, name: str, allowed: set[str], where: str = "root") -> dict:
    """A required table of `raw`, rejecting keys the loader does not know."""
    section = _require(raw, name, where)
    path = name if where == "root" else f"{where}.{name}"
    _reject_unknown(section, allowed, path)
    return section


def _parse_chunking(raw: dict) -> ChunkingConfig:
    section = _section(
        raw, "chunking",
        {"chunker_version", "target_tokens", "overlap_tokens", "text_lines_per_locator"},
    )
    chunking = ChunkingConfig(
        chunker_version=str(_require(section, "chunker_version", "chunking")),
        target_tokens=int(_require(section, "target_tokens", "chunking")),
        overlap_tokens=int(_require(section, "overlap_tokens", "chunking")),
        text_lines_per_locator=int(_require(section, "text_lines_per_locator", "chunking")),
    )
    if chunking.target_tokens <= 0:
        raise ConfigError("target_tokens must be positive")
    if chunking.overlap_tokens < 0 or chunking.overlap_tokens >= chunking.target_tokens:
        raise ConfigError("overlap_tokens must be in [0, target_tokens)")
    if chunking.text_lines_per_locator <= 0:
        raise ConfigError("text_lines_per_locator must be positive")
    return chunking


def _parse_keyword(seams: dict) -> KeywordSeamConfig:
    section = _section(seams, "keyword", {"enabled", "limit", "min_token_len"}, "seams")
    keyword = KeywordSeamConfig(
        enabled=bool(_require(section, "enabled", "seams.keyword")),
        limit=int(_require(section, "limit", "seams.keyword")),
        min_token_len=int(_require(section, "min_token_len", "seams.keyword")),
    )
    if keyword.limit <= 0:
        raise ConfigError("seams.keyword.limit must be positive")
    if keyword.min_token_len < 1:
        raise ConfigError("seams.keyword.min_token_len must be >= 1")
    return keyword


def _parse_embed(seams: dict) -> EmbedSeamConfig:
    section = _section(
        seams, "embed", {"enabled", "limit", "only_quota", "min_similarity"}, "seams")
    embed = EmbedSeamConfig(
        enabled=bool(_require(section, "enabled", "seams.embed")),
        limit=int(_require(section, "limit", "seams.embed")),
        only_quota=int(_require(section, "only_quota", "seams.embed")),
        min_similarity=float(_require(section, "min_similarity", "seams.embed")),
    )
    if embed.limit <= 0:
        raise ConfigError("seams.embed.limit must be positive")
    if embed.only_quota < 0:
        raise ConfigError("seams.embed.only_quota must be >= 0")
    if not -1.0 <= embed.min_similarity <= 1.0:
        raise ConfigError("seams.embed.min_similarity must be in [-1, 1]")
    return embed


def _parse_prereq(seams: dict) -> PrereqSeamConfig:
    section = _section(seams, "prereq", {"enabled", "decay", "directions", "limit"}, "seams")
    prereq = PrereqSeamConfig(
        enabled=bool(_require(section, "enabled", "seams.prereq")),
        decay=float(_require(section, "decay", "seams.prereq")),
        directions=tuple(str(value) for value in _require(section, "directions", "seams.prereq")),
        limit=int(_require(section, "limit", "seams.prereq")),
    )
    bad = set(prereq.directions) - {"self", "upstream", "downstream"}
    if bad:
        raise ConfigError(f"unknown prereq directions: {sorted(bad)}")
    if not prereq.directions:
        raise ConfigError("seams.prereq.directions must not be empty")
    if prereq.limit <= 0:
        raise ConfigError("seams.prereq.limit must be positive")
    return prereq


def _parse_fusion(raw: dict) -> FusionConfig:
    section = _section(raw, "fusion", {"cited_set_size", "min_score", "dedupe"})
    fusion = FusionConfig(
        cited_set_size=int(_require(section, "cited_set_size", "fusion")),
        min_score=float(_require(section, "min_score", "fusion")),
        dedupe=bool(_require(section, "dedupe", "fusion")),
    )
    if fusion.cited_set_size <= 0:
        raise ConfigError("fusion.cited_set_size must be positive")
    if not 0.0 <= fusion.min_score <= 1.0:
        raise ConfigError("fusion.min_score must be in [0, 1]")
    return fusion


def _parse_planning(raw: dict) -> PlanningConfig:
    keys = set(PlanningConfig.__dataclass_fields__)
    section = _section(raw, "planning", keys)
    planning = PlanningConfig(**{key: int(_require(section, key, "planning")) for key in keys})
    if any(value <= 0 for value in vars(planning).values()):
        raise ConfigError("planning limits must be positive")
    if planning.max_search_queries > 8 or planning.max_course_codes > 20 or planning.max_documents > 6:
        raise ConfigError("planning limits exceed the bounded plan size")
    return planning


def _parse_research(raw: dict) -> ResearchConfig:
    keys = set(ResearchConfig.__dataclass_fields__)
    section = _section(raw, "research", keys)
    research = ResearchConfig(**{key: int(_require(section, key, "research")) for key in keys})
    if any(value <= 0 for value in vars(research).values()):
        raise ConfigError("research limits must be positive")
    # max_candidates is also enforced by the strict state model; asking the
    # config for more would be silently ignored, so reject it instead.
    if research.max_candidates > 6:
        raise ConfigError("research.max_candidates exceeds the bounded state size")
    return research


def _parse_simgraph(raw: dict) -> SimgraphConfig:
    section = _section(
        raw, "simgraph",
        {
            "level_cut_distances",
            "linkage",
            "walk_top_margin",
            "walk_min_similarity",
            "walk_min_subtree",
        },
    )
    simgraph = SimgraphConfig(
        level_cut_distances=tuple(
            float(value) for value in _require(section, "level_cut_distances", "simgraph")
        ),
        linkage=str(_require(section, "linkage", "simgraph")),
        walk_top_margin=float(_require(section, "walk_top_margin", "simgraph")),
        walk_min_similarity=float(_require(section, "walk_min_similarity", "simgraph")),
        walk_min_subtree=int(_require(section, "walk_min_subtree", "simgraph")),
    )
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
        raise ConfigError("simgraph.linkage must be one of average, complete, single, weighted")
    if simgraph.walk_min_subtree < 0:
        raise ConfigError("simgraph.walk_min_subtree must be >= 0")
    return simgraph


def load_retrieval_config(
    path: Path | None = None, settings: Settings | None = None
) -> RetrievalConfig:
    path = path or _CONFIG_PATH
    settings = settings or load_settings()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    _reject_unknown(
        raw, {"version", "chunking", "seams", "fusion", "simgraph", "planning", "research"}, "root"
    )
    version = _require(raw, "version", "root")
    chunking = _parse_chunking(raw)
    seams_raw = _require(raw, "seams", "root")
    _reject_unknown(seams_raw, {"keyword", "embed", "prereq"}, "seams")
    seams = SeamsConfig(
        keyword=_parse_keyword(seams_raw),
        embed=_parse_embed(seams_raw),
        prereq=_parse_prereq(seams_raw),
    )
    fusion = _parse_fusion(raw)
    simgraph = _parse_simgraph(raw)
    planning = _parse_planning(raw)
    research = _parse_research(raw)
    _check_budget(chunking, fusion.cited_set_size, settings)
    return RetrievalConfig(
        version=int(version), chunking=chunking, seams=seams, fusion=fusion, simgraph=simgraph,
        planning=planning, research=research,
    )
