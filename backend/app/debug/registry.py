"""Allowlisted debug knobs. Secrets, auth, database, and routing stay fixed."""

from __future__ import annotations

import math
from dataclasses import replace

from app.config import Settings
from app.generation.answer import SYSTEM_PROMPT
from app.retrieval.config import RetrievalConfig

DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPT


class OverrideError(ValueError):
    """A debug override is unknown, out of bounds, or changes an immutable setting."""


def _field(name: str, label: str, group: str, kind: str, description: str,
           *, minimum=None, maximum=None, choices=()) -> dict:
    return {"name": name, "label": label, "group": group, "type": kind,
            "description": description, "minimum": minimum, "maximum": maximum,
            "choices": list(choices)}


SETTINGS_FIELDS = (
    _field("llm_max_output_tokens", "Max output tokens", "Generation", "int",
           "Per-call output cap.", minimum=16, maximum=8192),
    _field("llm_timeout_seconds", "Provider timeout (s)", "Generation", "float",
           "One provider call's timeout.", minimum=1, maximum=1800),
    _field("llm_sampling_policy", "Sampling policy", "Generation", "str",
           "greedy is reproducible; model_default uses the model's own settings.",
           choices=("greedy", "model_default")),
    _field("llm_reasoning_effort", "Reasoning effort", "Generation", "str",
           "Empty uses the model default.", choices=("", "none", "minimal", "low",
                                                    "medium", "high", "xhigh", "max")),
    _field("tools_enabled", "Typed tools", "Generation", "bool",
           "Allow the answer path to use typed tools."),
    _field("answer_citation_repair_enabled", "Citation repair", "Generation", "bool",
           "Retry a draft whose citations do not validate."),
    _field("tool_max_calls", "Max tool calls", "Generation", "int",
           "Per-operation typed tool call cap.", minimum=1, maximum=100),
    _field("llm_context_window_tokens", "Context window", "Compaction", "int",
           "Model window used for compaction accounting.", minimum=1000, maximum=2000000),
    _field("conversation_context_max_bytes", "Context hard cap (bytes)", "Compaction", "int",
           "Rendered history plus artifact context ceiling.", minimum=1024, maximum=1000000),
    _field("conversation_compaction_enabled", "Auto compaction", "Compaction", "bool",
           "Summarize older turns at the threshold."),
    _field("conversation_compaction_threshold", "Compaction threshold", "Compaction", "float",
           "Fraction of the context window that triggers a checkpoint.", minimum=0.05,
           maximum=0.95),
    _field("conversation_compaction_keep_turns", "Kept raw turns", "Compaction", "int",
           "Recent turns kept verbatim after a checkpoint.", minimum=1, maximum=50),
    _field("conversation_compaction_max_output_tokens", "Handoff output cap", "Compaction", "int",
           "Maximum tokens for one compacted memory.", minimum=128, maximum=8192),
    _field("conversation_compaction_timeout_seconds", "Handoff timeout (s)", "Compaction",
           "float", "One checkpoint provider call's timeout.", minimum=1, maximum=1800),
    _field("conversation_compaction_token_budget", "Handoff token budget", "Compaction", "int",
           "Accounting ceiling for one checkpoint; 0 derives from the window.",
           minimum=0, maximum=20000000),
    _field("conversation_token_estimate_bytes", "Bytes per token", "Compaction", "int",
           "Heuristic used for context accounting.", minimum=1, maximum=16),
    _field("query_planning_enabled", "Query planning", "Retrieval", "bool",
           "Read the catalog directory before retrieval."),
    _field("query_planning_token_budget", "Planning token budget", "Retrieval", "int",
           "Accounting ceiling for the planning call.", minimum=1000, maximum=2000000),
    _field("advising_research_enabled", "Research advising", "Retrieval", "bool",
           "Use the multi-step research advisor; requires tools."),
    _field("research_max_calls", "Research model calls", "Research", "int",
           "Model calls per research run.", minimum=1, maximum=100),
    _field("research_max_tool_calls", "Research tool calls", "Research", "int",
           "Tool calls per research run.", minimum=1, maximum=100),
    _field("research_operation_timeout_seconds", "Research timeout (s)", "Research", "float",
           "Whole research run deadline.", minimum=1, maximum=1800),
    _field("research_operation_token_budget", "Research token budget", "Research", "int",
           "Whole research run accounting ceiling.", minimum=1000, maximum=20000000),
    _field("research_max_prompt_bytes", "Research prompt cap", "Research", "int",
           "Bounded research prompt bytes.", minimum=1024, maximum=8000000),
    _field("research_max_output_tokens", "Research output cap", "Research", "int",
           "Per-call research output cap.", minimum=16, maximum=8192),
    _field("operation_timeout_seconds", "Operation timeout (s)", "Operations", "float",
           "One answer operation's deadline.", minimum=1, maximum=1800),
    _field("operation_max_calls", "Operation model calls", "Operations", "int",
           "Model calls per answer operation.", minimum=1, maximum=100),
    _field("operation_token_budget", "Operation token budget", "Operations", "int",
           "Accounting ceiling per answer operation.", minimum=1, maximum=20000000),
    _field("admission_max_pending_workspace", "Pending per workspace", "Admission", "int",
           "Outstanding turn cap per workspace; 0 disables.", minimum=0, maximum=100000),
    _field("admission_max_pending_global", "Pending global", "Admission", "int",
           "Outstanding turn cap globally; 0 disables.", minimum=0, maximum=100000),
)

RETRIEVAL_FIELDS = (
    _field("seams.keyword.limit", "Keyword limit", "Retrieval seams", "int",
           "Keyword candidates per query.", minimum=1, maximum=500),
    _field("seams.keyword.min_token_len", "Keyword min token", "Retrieval seams", "int",
           "Minimum normalized token length.", minimum=1, maximum=20),
    _field("seams.embed.limit", "Embedding limit", "Retrieval seams", "int",
           "Embedding candidates per query.", minimum=1, maximum=500),
    _field("seams.embed.only_quota", "Embedding quota", "Retrieval seams", "int",
           "Embedding-only quota before fusion.", minimum=1, maximum=500),
    _field("seams.embed.min_similarity", "Minimum similarity", "Retrieval seams", "float",
           "Cosine floor for embedding candidates.", minimum=-1, maximum=1),
    _field("seams.prereq.limit", "Prereq limit", "Retrieval seams", "int",
           "Prerequisite-graph candidates.", minimum=1, maximum=500),
    _field("seams.prereq.decay", "Prereq decay", "Retrieval seams", "float",
           "Graph distance decay.", minimum=0, maximum=1),
    _field("fusion.cited_set_size", "Cited set size", "Fusion", "int",
           "Passages kept for the answer.", minimum=1, maximum=50),
    _field("fusion.min_score", "Minimum fused score", "Fusion", "float",
           "Drop fused candidates below this score.", minimum=0, maximum=1),
    _field("fusion.dedupe", "Dedupe cited text", "Fusion", "bool",
           "Collapse identical cited passages."),
    _field("planning.max_search_queries", "Planning searches", "Planning", "int",
           "Model-proposed searches per plan.", minimum=1, maximum=20),
    _field("planning.max_course_codes", "Planning course codes", "Planning", "int",
           "Course codes per plan.", minimum=1, maximum=50),
    _field("planning.max_documents", "Planning documents", "Planning", "int",
           "Documents per plan.", minimum=1, maximum=20),
    _field("planning.question_slots", "Question slots", "Planning", "int",
           "Question hits in planned context.", minimum=1, maximum=50),
    _field("planning.document_chunks", "Document chunks", "Planning", "int",
           "Passages per planned document.", minimum=1, maximum=20),
    _field("planning.search_slots", "Search slots", "Planning", "int",
           "Hits per planned search.", minimum=1, maximum=20),
    _field("planning.context_size", "Planned context size", "Planning", "int",
           "Passages in a planned context.", minimum=1, maximum=200),
    _field("research.max_candidates", "Research candidates", "Research", "int",
           "Programs considered in one comparison.", minimum=1, maximum=50),
    _field("research.max_evidence_items", "Research evidence items", "Research", "int",
           "Evidence register ceiling.", minimum=1, maximum=10000),
    _field("research.no_progress_rounds", "No-progress rounds", "Research", "int",
           "Rounds before a finish is offered.", minimum=1, maximum=20),
)

_SETTINGS_BY_NAME = {field["name"]: field for field in SETTINGS_FIELDS}
_RETRIEVAL_BY_NAME = {field["name"]: field for field in RETRIEVAL_FIELDS}


def _coerce(field: dict, value):
    kind = field["type"]
    if kind == "bool":
        if type(value) is not bool:
            raise OverrideError(f"{field['name']} must be true or false")
        return value
    if kind == "int":
        if type(value) is not int:
            raise OverrideError(f"{field['name']} must be an integer")
    elif kind == "float":
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise OverrideError(f"{field['name']} must be a finite number")
        value = float(value)
    elif kind == "str":
        if not isinstance(value, str):
            raise OverrideError(f"{field['name']} must be a string")
    else:  # pragma: no cover - registry construction
        raise OverrideError(f"unsupported field type {kind}")
    if field.get("choices") and value not in field["choices"]:
        raise OverrideError(f"{field['name']} must be one of {field['choices']}")
    if field.get("minimum") is not None and value < field["minimum"]:
        raise OverrideError(f"{field['name']} must be at least {field['minimum']}")
    if field.get("maximum") is not None and value > field["maximum"]:
        raise OverrideError(f"{field['name']} must be at most {field['maximum']}")
    return value


def validate_settings(base: Settings, overrides: dict) -> Settings:
    unknown = set(overrides) - set(_SETTINGS_BY_NAME)
    if unknown:
        raise OverrideError(f"unknown settings: {sorted(unknown)}")
    values = {}
    for name, value in overrides.items():
        values[name] = _coerce(_SETTINGS_BY_NAME[name], value)
    if values.get("llm_reasoning_effort") == "":
        values["llm_reasoning_effort"] = None
    try:
        return replace(base, **values)
    except (TypeError, ValueError) as exc:
        raise OverrideError(str(exc)) from None


def _leaf(path: str, value):
    parts = path.split(".")
    nested = value
    for part in reversed(parts):
        nested = {part: nested}
    return nested


def _replace_tree(obj, changes: dict):
    kwargs = {}
    for key, value in changes.items():
        if not hasattr(obj, key):
            raise OverrideError(f"unknown retrieval setting: {key}")
        kwargs[key] = _replace_tree(getattr(obj, key), value) if isinstance(value, dict) else value
    return replace(obj, **kwargs)


def validate_retrieval(base: RetrievalConfig, overrides: dict) -> RetrievalConfig:
    unknown = set(overrides) - set(_RETRIEVAL_BY_NAME)
    if unknown:
        raise OverrideError(f"unknown retrieval settings: {sorted(unknown)}")
    changes = {}
    for name, value in overrides.items():
        changes.update(_leaf(name, _coerce(_RETRIEVAL_BY_NAME[name], value)))
    try:
        return _replace_tree(base, changes)
    except TypeError as exc:
        raise OverrideError(str(exc)) from None


def _get_path(obj, path: str):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def settings_snapshot(base: Settings) -> list[dict]:
    return [{**field, "value": getattr(base, field["name"])}
            for field in SETTINGS_FIELDS]


def retrieval_snapshot(base: RetrievalConfig) -> list[dict]:
    return [{**field, "value": _get_path(base, field["name"])}
            for field in RETRIEVAL_FIELDS]
