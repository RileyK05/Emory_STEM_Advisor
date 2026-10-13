"""Connection profile contracts. Secret material is omitted from public views."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

ProviderName = Literal[
    "openai", "anthropic", "google", "openai-compatible", "hf-local",
    "openai-decisions", "cloudflare-clef",
]
RouteRole = Literal["standard", "complex", "fast_generation", "decision"]
Capability = Literal["structured_output", "tools", "streaming", "vision", "batch", "decision"]
DataScope = Literal["institutional_corpus", "user_supplied_data", "private_workspace"]

PROVIDERS = frozenset({
    "openai", "anthropic", "google", "openai-compatible", "hf-local",
    "openai-decisions", "cloudflare-clef",
})
ROLES = frozenset({"standard", "complex", "fast_generation", "decision"})
CAPABILITIES = frozenset({"structured_output", "tools", "streaming", "vision", "batch", "decision"})
DATA_SCOPES = frozenset({"institutional_corpus", "user_supplied_data", "private_workspace"})
DEVELOPMENT_WORKSPACE = "development"
DECISION_PROVIDERS = frozenset({"openai-decisions", "cloudflare-clef"})


def required_data_scopes(workspace_key: str, *, authoring: bool = False) -> frozenset[str]:
    """Data scopes a model profile must be approved for to serve this workspace."""
    scopes = {"user_supplied_data"} if authoring else {"institutional_corpus", "user_supplied_data"}
    if workspace_key != DEVELOPMENT_WORKSPACE:
        scopes.add("private_workspace")
    return frozenset(scopes)


class ProfileValidationError(ValueError):
    """A profile is invalid or incompatible with its declared use."""


class ConnectionUnavailable(RuntimeError):
    """No route profile can serve the requested role, scope, and capability set."""


class MissingCredential(RuntimeError):
    """A provider requires a credential that was not configured."""


class CredentialUnreadable(RuntimeError):
    """Stored credentials exist but cannot be decrypted with the active key."""


@dataclass(frozen=True)
class ProfileCreate:
    name: str
    provider: ProviderName
    model_id: str
    roles: frozenset[RouteRole]
    capabilities: frozenset[Capability]
    data_scopes: frozenset[DataScope]
    base_url: str | None = None
    account_id: str | None = None
    provider_options: Mapping[str, Any] = field(default_factory=dict)
    secret: Mapping[str, str] | None = field(default=None, repr=False)
    enabled: bool = True
    priority: int = 100


@dataclass(frozen=True)
class ProfileUpdate:
    expected_revision: int
    name: str
    provider: ProviderName
    model_id: str
    roles: frozenset[RouteRole]
    capabilities: frozenset[Capability]
    data_scopes: frozenset[DataScope]
    base_url: str | None = None
    account_id: str | None = None
    provider_options: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    priority: int = 100


@dataclass(frozen=True)
class StoredProfile:
    profile_id: UUID
    name: str
    provider: str
    model_id: str
    base_url: str | None
    account_id: str | None
    secret_ciphertext: bytes | None = field(repr=False)
    provider_options: Mapping[str, Any]
    roles: frozenset[str]
    capabilities: frozenset[str]
    data_scopes: frozenset[str]
    enabled: bool
    priority: int
    revision: int
    created_at: datetime
    updated_at: datetime
    eval_passed: bool = False

    @property
    def secret_configured(self) -> bool:
        return self.secret_ciphertext is not None


@dataclass(frozen=True)
class ProfileView:
    profile_id: UUID
    name: str
    provider: str
    model_id: str
    base_url: str | None
    account_id: str | None
    secret_configured: bool
    provider_options: Mapping[str, Any]
    roles: frozenset[str]
    capabilities: frozenset[str]
    data_scopes: frozenset[str]
    enabled: bool
    priority: int
    revision: int
    created_at: datetime
    updated_at: datetime
    eval_passed: bool = False


@dataclass(frozen=True)
class EvaluationEvidence:
    evaluation_id: UUID
    profile_id: UUID
    profile_revision: int
    provider: str
    model_id: str
    suite_hash: str
    status: Literal["passed", "failed"]
    metrics: Mapping[str, Any]


@dataclass(frozen=True)
class CredentialDiagnostic:
    status: Literal["not_required", "configured", "missing", "unreadable"]
    message: str


def validate_profile(data: ProfileCreate | ProfileUpdate) -> None:
    provider = data.provider
    if provider not in PROVIDERS:
        raise ProfileValidationError(f"unsupported provider: {provider!r}")
    if not data.name.strip() or len(data.name.strip()) > 120:
        raise ProfileValidationError("profile name must contain 1-120 characters")
    if not data.model_id.strip() or len(data.model_id.strip()) > 200:
        raise ProfileValidationError("model id must contain 1-200 characters")
    if not data.roles or not data.roles <= ROLES:
        raise ProfileValidationError("profile roles must be a non-empty supported set")
    if not data.capabilities <= CAPABILITIES:
        raise ProfileValidationError("profile contains unsupported capabilities")
    if not data.data_scopes <= DATA_SCOPES:
        raise ProfileValidationError("profile contains unsupported data scopes")
    if provider in DECISION_PROVIDERS and data.roles != frozenset({"decision"}):
        raise ProfileValidationError("decision-only providers may only serve the decision role")
    if provider not in DECISION_PROVIDERS and "decision" in data.roles:
        raise ProfileValidationError("decision roles require a decision-only provider profile")
    if provider == "openai-compatible" and not data.base_url:
        raise ProfileValidationError("OpenAI-compatible profiles require a base URL")
    if data.base_url:
        from urllib.parse import urlsplit
        parsed = urlsplit(data.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ProfileValidationError("base_url must be an HTTP(S) URL without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ProfileValidationError("base_url cannot contain a query or fragment")
    if provider == "cloudflare-clef" and not data.account_id:
        raise ProfileValidationError("Cloudflare Clef profiles require an account id")
    if provider != "cloudflare-clef" and data.account_id is not None:
        raise ProfileValidationError("account_id is only valid for Cloudflare Clef profiles")
    if provider == "hf-local" and getattr(data, "secret", None):
        raise ProfileValidationError("hf-local profiles do not use API credentials")
    if data.priority < 0 or data.priority > 1_000_000:
        raise ProfileValidationError("priority must be between 0 and 1000000")
    options = dict(data.provider_options)
    if set(options) - {"sampling_policy", "reasoning_effort"}:
        raise ProfileValidationError("provider_options contains unsupported keys")
    sampling = options.get("sampling_policy", "greedy")
    effort = options.get("reasoning_effort")
    if not isinstance(sampling, str) or sampling not in {"greedy", "model_default"}:
        raise ProfileValidationError("sampling_policy must be greedy or model_default")
    if (effort is not None and not isinstance(effort, str)
            or effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}):
        raise ProfileValidationError("reasoning_effort is unsupported")
    if sampling == "greedy" and effort not in {None, "none"}:
        raise ProfileValidationError("reasoning effort requires explicit model_default sampling policy")
    if provider == "hf-local" and sampling != "greedy":
        raise ProfileValidationError("hf-local requires greedy sampling")
    if effort is not None and provider not in {"openai", "openai-compatible"}:
        raise ProfileValidationError("this provider does not support reasoning_effort")

