"""Deterministic task router. It selects configured profiles; it does not call models."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

GENERATION_ROLES = frozenset({"standard", "complex", "fast_generation"})


@dataclass(frozen=True)
class RoutingConfig:
    version: int
    default_role: str
    uncertain_role: str
    max_quality_escalations: int
    complex_terms: tuple[str, ...]
    fast_job_kinds: tuple[str, ...]
    decision_job_kinds: tuple[str, ...]
    complex_token_threshold: int
    course_lookup_patterns: tuple[str, ...] = ()
    simple_lookup_max_words: int = 10

    @classmethod
    def load(cls, path: str | Path) -> RoutingConfig:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {"version", "default_role", "uncertain_role", "max_quality_escalations", "signals"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown routing keys: {sorted(unknown)}")
        signals = data.get("signals", {})
        signal_keys = {"complex_terms", "fast_job_kinds", "decision_job_kinds",
                       "complex_token_threshold", "course_lookup_patterns",
                       "simple_lookup_max_words"}
        if set(signals) - signal_keys:
            raise ValueError(f"unknown routing signal keys: {sorted(set(signals) - signal_keys)}")
        if type(data.get("version")) is not int or data.get("version") not in {1, 2}:
            raise ValueError("unsupported routing config version")
        escalations = data.get("max_quality_escalations", 1)
        if type(escalations) is not int or escalations not in (0, 1):
            raise ValueError("max_quality_escalations must be 0 or 1")
        default_role = data.get("default_role", "standard")
        uncertain_role = data.get("uncertain_role", "complex")
        if default_role not in GENERATION_ROLES:
            raise ValueError("default_role must be a generation role")
        if uncertain_role not in GENERATION_ROLES:
            raise ValueError("uncertain_role must be a generation role")
        for key in ("complex_terms", "fast_job_kinds", "decision_job_kinds"):
            value = signals.get(key, ())
            if (not isinstance(value, list) or any(not isinstance(item, str) or not item
                                                   for item in value)):
                raise ValueError(f"routing {key} must be an array of non-empty strings")
        threshold = signals.get("complex_token_threshold", 1200)
        if type(threshold) is not int or threshold < 1:
            raise ValueError("complex_token_threshold must be a positive integer")
        patterns = signals.get("course_lookup_patterns", [])
        if not isinstance(patterns, list) or any(not isinstance(value, str) or not value
                                                 for value in patterns):
            raise ValueError("course_lookup_patterns must be an array of regular expressions")
        for pattern in patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError("course_lookup_patterns contains an invalid regular expression") from exc
        simple_max_words = signals.get("simple_lookup_max_words", 10)
        if type(simple_max_words) is not int or not 1 <= simple_max_words <= 20:
            raise ValueError("simple_lookup_max_words must be between 1 and 20")
        return cls(data.get("version"), data.get("default_role", "standard"),
            data.get("uncertain_role", "complex"), escalations,
            tuple(signals.get("complex_terms", ())), tuple(signals.get("fast_job_kinds", ())),
            tuple(signals.get("decision_job_kinds", ())),
            threshold, tuple(patterns), simple_max_words)


@dataclass(frozen=True)
class Candidate:
    profile_id: str
    role: str
    provider: str
    model: str
    capabilities: frozenset[str] = frozenset()
    data_scopes: frozenset[str] = frozenset()
    eval_passed: bool = False

    def serves(self, role: str, capabilities: frozenset[str], data_scope: str | None,
               required_data_scopes: frozenset[str]) -> bool:
        return (self.role == role and capabilities <= self.capabilities
                and (data_scope is None or data_scope in self.data_scopes)
                and required_data_scopes <= self.data_scopes)


@dataclass(frozen=True)
class RouteDecision:
    role: str
    candidate: Candidate
    reason: str


def classify_role(config: RoutingConfig, *, query: str, difficulty: str | None = None,
                  job_kind: str | None = None) -> tuple[str, str]:
    if job_kind and job_kind in config.decision_job_kinds:
        return "decision", "decision task kind"
    if job_kind and job_kind in config.fast_job_kinds:
        return "fast_generation", "independent batch generation"
    level = (difficulty or "unknown").lower()
    if level in {"high", "hard", "complex"}:
        return "complex", "explicit high difficulty"
    lowered = query.lower()
    if (_estimated_tokens(query) >= config.complex_token_threshold
            or any(term in lowered for term in config.complex_terms)):
        return "complex", "complexity signal"
    if level in {"unknown", "uncertain", ""} and _matches_simple_course_lookup(config, query):
        return "standard", "exact course-code lookup intent"
    if level in {"unknown", "uncertain", ""}:
        return config.uncertain_role, "uncertain difficulty defaults to stronger route"
    return config.default_role, "standard task"


def _estimated_tokens(text: str) -> int:
    """A cheap token-count estimate (no tokenizer in this pure module)."""
    return max(1, (len(text) + 3) // 4)


def _matches_simple_course_lookup(config: RoutingConfig, query: str) -> bool:
    normalized = " ".join(query.split())
    if len(normalized.split()) > config.simple_lookup_max_words:
        return False
    return any(re.fullmatch(pattern, normalized, re.IGNORECASE)
               for pattern in config.course_lookup_patterns)


def choose_route(config: RoutingConfig, candidates: Sequence[Candidate], *, query: str,
                 difficulty: str | None = None, job_kind: str | None = None,
                 required_capabilities: frozenset[str] = frozenset(),
                 data_scope: str | None = None,
                 required_data_scopes: frozenset[str] = frozenset()) -> RouteDecision:
    role, reason = classify_role(config, query=query, difficulty=difficulty, job_kind=job_kind)
    def serving(for_role):
        return [c for c in candidates
                if c.serves(for_role, required_capabilities, data_scope, required_data_scopes)]

    eligible = serving(role)
    if not eligible and role == "decision":
        role, reason = "complex", "no decision candidate; use stronger structured classifier"
        eligible = serving(role)
    eligible = [c for c in eligible if c.eval_passed]
    if not eligible:
        raise LookupError(f"no eval-approved candidate for route role {role!r}")
    return RouteDecision(role, eligible[0], reason)


def quality_fallback(config: RoutingConfig, chosen: Candidate, candidates: Sequence[Candidate], *,
                     required_capabilities: frozenset[str] = frozenset(),
                     data_scope: str | None = None,
                     required_data_scopes: frozenset[str] = frozenset()) -> Candidate | None:
    """Return at most one stronger eval-approved candidate for bounded escalation."""
    if config.max_quality_escalations == 0 or chosen.role == "complex":
        return None
    return next((c for c in candidates if c.eval_passed and c.serves(
        "complex", required_capabilities, data_scope, required_data_scopes)), None)
