"""Config. Everything is env-driven; nothing model-specific is hardcoded."""

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HF_MODEL_ID = "openbmb/MiniCPM-2B-128k"
_DEFAULT_HF_STOP_TOKEN_STRINGS = ("<|im_end|>",)


def _load_dotenv() -> None:
    """Load `REPO_ROOT/.env` into the environment without overriding anything.

    Real environment variables and shell exports win, so a key in the shell
    always beats the file (intentional). Runs at import, before any setting is
    read. Missing file, blank lines, and `#` comments are ignored. Set
    `ADVISOR_SKIP_DOTENV=1` to skip the import-time load entirely (the test
    suite does, so a developer's `.env` cannot change test behavior).
    """
    path = _REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


if os.environ.get("ADVISOR_SKIP_DOTENV") != "1":
    _load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # Selected local/native provider; native model and key are configured below.
    llm_provider: str
    # Hugging Face model id for the local experimentation provider.
    hf_model_id: str
    # Context ceiling for local experiments. Deliberately far below the
    # model's max: full attention over 128k is not feasible on consumer
    # hardware. The local model validates the pipeline, not long context.
    hf_max_context: int
    hf_max_new_tokens: int
    # Embedding provider for the RAG index. Currently "granite" only;
    # "qwen3" is the planned fallback if retrieval quality disappoints.
    embedding_provider: str
    # Hugging Face model id for the embedding provider.
    embedding_model_id: str
    # Max tokens per embedded text. Granite's context is 512; chunkers
    # must respect this ceiling.
    embedding_max_tokens: int
    # Postgres connection string. None means the dev/test embedded database
    # (pgserver) is used; production sets DATABASE_URL.
    database_url: str | None
    # Collection the HTTP API serves when a request does not name one.
    default_collection: str
    # Where ingest keeps raw uploads (`raw/`) and the dev database
    # (`pgserver/`). Gitignored. Tests point it at a temp dir.
    data_dir: Path
    # Optional exact tokenizer tokens that terminate a local model turn.
    # None selects defaults for the configured model; () disables additions.
    hf_stop_token_strings: tuple[str, ...] | None = None
    llm_model_id: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)
    llm_base_url: str | None = None
    upload_max_bytes: int = 20 * 1024 * 1024
    request_max_bytes: int = 21 * 1024 * 1024
    readiness_timeout_seconds: int = 3
    embedding_device: str = "cpu"
    profile_encryption_key: str | None = field(default=None, repr=False)
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 1024
    routing_enabled: bool = False
    routing_eval_suite_hash: str | None = None
    admin_api_enabled: bool = False
    # Institutional Admin and Debug surfaces. Both still require an admin
    # principal; these flags are deployment kill switches, not authorization.
    admin_panel_enabled: bool = True
    debug_panel_enabled: bool = True
    debug_session_ttl_seconds: int = 28800
    run_handler_factory: str = "app.generation.artifacts:default_handlers"
    operation_timeout_seconds: float = 120.0
    operation_max_calls: int = 2
    operation_token_budget: int = 16_000
    # The hard conversation context budget. `load_settings` derives a default
    # from the model window when CONVERSATION_CONTEXT_MAX_BYTES is unset.
    conversation_context_max_bytes: int = 12000
    # Context window used for compaction accounting. Cloud providers have no
    # portable way to report it, so it is explicit and defaults to 128k.
    llm_context_window_tokens: int = 128_000
    conversation_compaction_enabled: bool = True
    # Compact when the rendered summary + recent history reaches this fraction
    # of the model context window (0.5 = 50%).
    conversation_compaction_threshold: float = 0.5
    # Raw recent turns kept verbatim after a checkpoint, so the next turn still
    # sees the immediate exchange without re-reading the whole transcript.
    conversation_compaction_keep_turns: int = 4
    conversation_compaction_max_output_tokens: int = 2048
    conversation_compaction_timeout_seconds: float = 180.0
    # 0 derives from the context window plus summary output and prompt overhead.
    conversation_compaction_token_budget: int = 0
    # Heuristic used for context accounting: UTF-8 bytes per token. It is an
    # estimate because provider tokenizers are model-specific.
    conversation_token_estimate_bytes: int = 4
    batch_operation_timeout_seconds: float = 172800.0

    llm_sampling_policy: str = "greedy"
    llm_reasoning_effort: str | None = None
    llm_compatible_thinking: str | None = None
    # Send GenerationRequest.cache_key as `prompt_cache_key` to a compatible
    # service (verified on LongCat: without it a shared prefix rarely hits).
    llm_compatible_cache_key: bool = False
    auth_mode: str = "development"
    auth_server_key: str | None = field(default=None, repr=False)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = field(default=None, repr=False)
    oidc_redirect_uri: str | None = None
    auth_membership_claim: str | None = None
    auth_membership_values: tuple[str, ...] = ()
    auth_verified_email_domains: tuple[str, ...] = ()
    # First-login promotion only. Once a user exists, role changes are made in
    # the Admin UI and are never overwritten by the bootstrap list.
    admin_bootstrap_emails: tuple[str, ...] = ()
    admin_bootstrap_subjects: tuple[str, ...] = ()
    auth_session_ttl_seconds: int = 28800
    auth_transaction_ttl_seconds: int = 300
    tools_enabled: bool = False
    answer_citation_repair_enabled: bool = False
    tool_max_calls: int = 4
    # Opt-in: before retrieval, the answering connection reads the scoped catalog
    # directory and proposes bounded searches (docs/plans/catalog-context.md).
    # The planning call gets its own call and reservation allowance inside the
    # same operation; reservation units are serialized prompt bytes.
    query_planning_enabled: bool = False
    query_planning_token_budget: int = 300_000
    # Opt-in research advising path (docs/plans/research-advisor-v1.md).
    # Feature precedence is explicit: when enabled it replaces the one-shot
    # query-planning route for that request. It requires a tools-capable
    # permitted provider and TOOLS_ENABLED; an unavailable research provider is
    # a clear feature-unavailable error, never a silent fallback to a materially
    # different advising workflow.
    advising_research_enabled: bool = False
    # One research run gets exactly one operation with these limits; they
    # replace the legacy answer limits for that operation rather than stacking
    # on top of them. Reservation units are serialized prompt bytes, so the
    # token budget is an accounting ceiling, not a promised token bill.
    research_max_calls: int = 12
    research_max_tool_calls: int = 24
    research_operation_timeout_seconds: float = 240.0
    research_operation_token_budget: int = 640_000
    research_max_prompt_bytes: int = 48_000
    research_max_output_tokens: int = 4096
    office_enabled: bool = False
    sandbox_remote_url: str | None = None
    sandbox_bearer_token: str | None = field(default=None, repr=False)
    sandbox_office_image: str = "emory-sandbox-office:7.4.7-v1"
    office_timeout_seconds: float = 60.0
    office_max_input_bytes: int = 16 * 1024 * 1024
    office_max_output_bytes: int = 32 * 1024 * 1024
    # Limit per pool role per process; conversation workers use two roles.
    # 0 disables pooling.
    db_pool_max_size: int = 20
    db_pool_timeout_seconds: float = 10.0
    # Server-side limits for pooled application connections, so a slow plan or a
    # request that leaves a transaction open cannot pin a slot indefinitely.
    # 0 disables the corresponding server setting.
    db_statement_timeout_ms: int = 30_000
    db_idle_transaction_timeout_ms: int = 60_000
    # Sync FastAPI dependencies and handlers share one AnyIO thread pool per
    # process. Size it for expected concurrent requests so a dependency waiting
    # for a pooled connection cannot starve the handler that releases it.
    http_threadpool_tokens: int = 40
    # Opt-in admission caps for outstanding conversation turns. 0 disables the
    # corresponding cap. The check and the job insert share one transaction and
    # an advisory lock, so the cap holds across replicas.
    admission_max_pending_workspace: int = 0
    admission_max_pending_global: int = 0

    def __post_init__(self) -> None:
        stop_tokens = self.hf_stop_token_strings
        if stop_tokens is None:
            stop_tokens = default_hf_stop_token_strings(self.hf_model_id)
            object.__setattr__(self, "hf_stop_token_strings", stop_tokens)
        if (not isinstance(stop_tokens, tuple) or len(stop_tokens) > 8
                or any(not isinstance(token, str) or not token.strip()
                       or len(token) > 128 or "\x00" in token for token in stop_tokens)
                or len(set(stop_tokens)) != len(stop_tokens)):
            raise ValueError(
                "HF_STOP_TOKEN_STRINGS must contain up to 8 unique nonempty strings of at most 128 characters"
            )
        if type(self.readiness_timeout_seconds) is not int or not 1 <= self.readiness_timeout_seconds <= 10:
            raise ValueError("READINESS_TIMEOUT_SECONDS must be between 1 and 10")
        if type(self.db_pool_max_size) is not int or not 0 <= self.db_pool_max_size <= 200:
            raise ValueError("DB_POOL_MAX_SIZE must be between 0 (disabled) and 200")
        if not math.isfinite(self.db_pool_timeout_seconds) or not 0.1 <= self.db_pool_timeout_seconds <= 120:
            raise ValueError("DB_POOL_TIMEOUT_SECONDS must be between 0.1 and 120")
        if type(self.db_statement_timeout_ms) is not int or not 0 <= self.db_statement_timeout_ms <= 600_000:
            raise ValueError("DB_STATEMENT_TIMEOUT_MS must be between 0 (disabled) and 600000")
        if (type(self.db_idle_transaction_timeout_ms) is not int
                or not 0 <= self.db_idle_transaction_timeout_ms <= 3_600_000):
            raise ValueError(
                "DB_IDLE_TRANSACTION_TIMEOUT_MS must be between 0 (disabled) and 3600000")
        if type(self.http_threadpool_tokens) is not int or not 1 <= self.http_threadpool_tokens <= 1000:
            raise ValueError("HTTP_THREADPOOL_TOKENS must be between 1 and 1000")
        for name, value in (
            ("ADMISSION_MAX_PENDING_WORKSPACE", self.admission_max_pending_workspace),
            ("ADMISSION_MAX_PENDING_GLOBAL", self.admission_max_pending_global),
        ):
            if type(value) is not int or not 0 <= value <= 100_000:
                raise ValueError(f"{name} must be between 0 (disabled) and 100000")
        if not math.isfinite(self.batch_operation_timeout_seconds) or not 3600 <= self.batch_operation_timeout_seconds <= 604800:
            raise ValueError("BATCH_OPERATION_TIMEOUT_SECONDS must be between 3600 and 604800")
        if not 1024 <= self.conversation_context_max_bytes <= 1_000_000:
            raise ValueError("CONVERSATION_CONTEXT_MAX_BYTES must be between 1024 and 1000000")
        if not 1_000 <= self.llm_context_window_tokens <= 2_000_000:
            raise ValueError("LLM_CONTEXT_WINDOW_TOKENS must be between 1000 and 2000000")
        if type(self.conversation_compaction_enabled) is not bool:
            raise ValueError("CONVERSATION_COMPACTION_ENABLED must be true or false")
        if (not math.isfinite(self.conversation_compaction_threshold)
                or not 0.05 <= self.conversation_compaction_threshold <= 0.95):
            raise ValueError("CONVERSATION_COMPACTION_THRESHOLD must be between 0.05 and 0.95")
        if not 1 <= self.conversation_compaction_keep_turns <= 50:
            raise ValueError("CONVERSATION_COMPACTION_KEEP_TURNS must be between 1 and 50")
        if not 128 <= self.conversation_compaction_max_output_tokens <= 8192:
            raise ValueError("CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS must be between 128 and 8192")
        if (not math.isfinite(self.conversation_compaction_timeout_seconds)
                or self.conversation_compaction_timeout_seconds <= 0):
            raise ValueError("CONVERSATION_COMPACTION_TIMEOUT_SECONDS must be positive and finite")
        if not 0 <= self.conversation_compaction_token_budget <= 20_000_000:
            raise ValueError("CONVERSATION_COMPACTION_TOKEN_BUDGET must be between 0 and 20000000")
        if not 1 <= self.conversation_token_estimate_bytes <= 16:
            raise ValueError("CONVERSATION_TOKEN_ESTIMATE_BYTES must be between 1 and 16")
        if self.llm_sampling_policy not in {"greedy", "model_default"}:
            raise ValueError("LLM_SAMPLING_POLICY must be greedy or model_default")
        if self.llm_reasoning_effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("LLM_REASONING_EFFORT is unsupported")
        if self.llm_compatible_thinking not in {None, "enabled", "disabled"}:
            raise ValueError("LLM_COMPATIBLE_THINKING must be enabled or disabled")
        if self.llm_sampling_policy == "greedy" and self.llm_reasoning_effort not in {None, "none"}:
            raise ValueError("reasoning effort requires explicit model_default sampling policy")
        if self.llm_provider == "hf-local" and (self.llm_sampling_policy != "greedy"
                or self.llm_reasoning_effort is not None):
            raise ValueError("hf-local requires greedy generation without reasoning effort")
        if not 1_000 <= self.query_planning_token_budget <= 2_000_000:
            raise ValueError("QUERY_PLANNING_TOKEN_BUDGET must be between 1000 and 2000000")
        if not 1 <= self.research_max_calls <= 100:
            raise ValueError("RESEARCH_MAX_CALLS must be between 1 and 100")
        if not 1 <= self.research_max_tool_calls <= 100:
            raise ValueError("RESEARCH_MAX_TOOL_CALLS must be between 1 and 100")
        if (not math.isfinite(self.research_operation_timeout_seconds)
                or self.research_operation_timeout_seconds <= 0
                or not 1_000 <= self.research_operation_token_budget <= 20_000_000
                or not 1_024 <= self.research_max_prompt_bytes <= 8_000_000
                or self.research_max_output_tokens <= 0):
            raise ValueError("research limits are invalid")
        if self.advising_research_enabled and not self.tools_enabled:
            raise ValueError("ADVISING_RESEARCH_ENABLED requires TOOLS_ENABLED=true")
        if (self.advising_research_enabled and self.llm_provider == "hf-local"
                and not self.routing_enabled):
            raise ValueError(
                "research advising requires a tools-capable cloud connection or evaluated routing"
            )
        if not 1 <= self.tool_max_calls <= 100:
            raise ValueError("TOOL_MAX_CALLS must be between 1 and 100")
        if self.tools_enabled and self.llm_provider == "hf-local" and not self.routing_enabled:
            raise ValueError("native tools require a capable cloud connection or evaluated routing")
        if self.auth_mode not in {"development", "oidc"}:
            raise ValueError("AUTH_MODE must be development or oidc")
        if not 300 <= self.auth_session_ttl_seconds <= 86400 or not 60 <= self.auth_transaction_ttl_seconds <= 900:
            raise ValueError("authentication lifetimes are invalid")
        if not 300 <= self.debug_session_ttl_seconds <= 86400:
            raise ValueError("DEBUG_SESSION_TTL_SECONDS must be between 300 and 86400")
        for name, values in (
            ("ADMIN_BOOTSTRAP_EMAILS", self.admin_bootstrap_emails),
            ("ADMIN_BOOTSTRAP_SUBJECTS", self.admin_bootstrap_subjects),
        ):
            if (not isinstance(values, tuple) or len(values) > 100
                    or any(not isinstance(value, str) or not value.strip()
                           or len(value) > 320 or "\x00" in value for value in values)):
                raise ValueError(f"{name} must contain up to 100 nonempty values")
        if any("@" not in value for value in self.admin_bootstrap_emails):
            raise ValueError("ADMIN_BOOTSTRAP_EMAILS entries must contain @")
        if (not math.isfinite(self.office_timeout_seconds) or not 1 <= self.office_timeout_seconds <= 300
                or not 1 <= self.office_max_input_bytes <= 16 * 1024 * 1024
                or not 1024 <= self.office_max_output_bytes <= 32 * 1024 * 1024):
            raise ValueError("Office limits are invalid")
        if self.office_enabled and not (self.sandbox_remote_url and self.sandbox_bearer_token):
            raise ValueError("Office requires an authenticated remote sandbox")
        if self.upload_max_bytes <= 0 or self.request_max_bytes <= 0:
            raise ValueError("UPLOAD_MAX_BYTES must be positive")
        if self.embedding_max_tokens <= 0 or self.hf_max_context <= 0:
            raise ValueError("model token ceilings must be positive")
        if (not math.isfinite(self.llm_timeout_seconds)
                or self.llm_timeout_seconds <= 0 or self.llm_max_output_tokens <= 0):
            raise ValueError("generation limits must be positive")
        if (not math.isfinite(self.operation_timeout_seconds)
                or self.operation_timeout_seconds <= 0
                or not 1 <= self.operation_max_calls <= 100
                or self.operation_token_budget < 1):
            raise ValueError("operation limits are invalid")


def load_settings() -> Settings:
    provider = os.environ.get("LLM_PROVIDER", "hf-local")
    data_dir = Path(os.environ.get("DATA_DIR") or _REPO_ROOT / "data")
    # CLI entrypoints run from backend/, while .env lives at the repo root.
    # Keep relative DATA_DIR values on the same catalog database in both places.
    if not data_dir.is_absolute():
        data_dir = _REPO_ROOT / data_dir
    hf_model_id = os.environ.get("HF_MODEL_ID", DEFAULT_HF_MODEL_ID)
    stop_tokens_raw = os.environ.get("HF_STOP_TOKEN_STRINGS")
    if stop_tokens_raw is None:
        hf_stop_token_strings = None
    else:
        try:
            parsed_stop_tokens = json.loads(stop_tokens_raw)
        except json.JSONDecodeError:
            raise ValueError("HF_STOP_TOKEN_STRINGS must be a JSON array of strings") from None
        if not isinstance(parsed_stop_tokens, list) or any(
            not isinstance(token, str) for token in parsed_stop_tokens
        ):
            raise ValueError("HF_STOP_TOKEN_STRINGS must be a JSON array of strings")
        hf_stop_token_strings = tuple(parsed_stop_tokens)
    key_variable = {
        "openai": "OPENAI_API_KEY", "openai-compatible": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(provider)
    hf_max_context = _int("HF_MAX_CONTEXT", 4096)
    bytes_per_token = _int("CONVERSATION_TOKEN_ESTIMATE_BYTES", 4)
    # A cloud model's window is not discoverable through the provider seam, so
    # it is explicit. The local model already declares its own ceiling.
    context_window = _int(
        "LLM_CONTEXT_WINDOW_TOKENS",
        hf_max_context if provider == "hf-local" else 128_000,
    )
    context_max_default = min(1_000_000, max(12_000, context_window * bytes_per_token))
    compaction_output = _int("CONVERSATION_COMPACTION_MAX_OUTPUT_TOKENS", 2048)
    compaction_budget = _int("CONVERSATION_COMPACTION_TOKEN_BUDGET", 0)
    if compaction_budget == 0:
        compaction_budget = min(20_000_000, context_window + compaction_output + 4096)
    return Settings(
        llm_provider=provider,
        hf_model_id=hf_model_id,
        hf_max_context=hf_max_context,
        hf_max_new_tokens=_int("HF_MAX_NEW_TOKENS", 256),
        hf_stop_token_strings=hf_stop_token_strings,
        embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "granite"),
        embedding_model_id=os.environ.get(
            "EMBEDDING_MODEL_ID", "ibm-granite/granite-embedding-125m-english"
        ),
        embedding_max_tokens=_int("EMBEDDING_MAX_TOKENS", 512),
        database_url=os.environ.get("DATABASE_URL") or None,
        default_collection=os.environ.get("DEFAULT_COLLECTION", "default"),
        data_dir=data_dir,
        llm_model_id=os.environ.get("LLM_MODEL_ID") or None,
        llm_api_key=os.environ.get("LLM_API_KEY") or (os.environ.get(key_variable) if key_variable else None),
        llm_base_url=os.environ.get("LLM_BASE_URL") or None,
        upload_max_bytes=_int("UPLOAD_MAX_BYTES", 20 * 1024 * 1024),
        embedding_device=os.environ.get("EMBEDDING_DEVICE", "cpu"),
        request_max_bytes=_int("REQUEST_MAX_BYTES", 21 * 1024 * 1024),
        readiness_timeout_seconds=_int("READINESS_TIMEOUT_SECONDS", 3),
        profile_encryption_key=os.environ.get("PROFILE_ENCRYPTION_KEY") or None,
        llm_timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
        llm_max_output_tokens=_int("LLM_MAX_OUTPUT_TOKENS", 1024),
        routing_enabled=os.environ.get("ROUTING_ENABLED", "false").lower() == "true",
        routing_eval_suite_hash=os.environ.get("ROUTING_EVAL_SUITE_HASH") or None,
        admin_api_enabled=os.environ.get("ADMIN_API_ENABLED", "false").lower() == "true",
        run_handler_factory=os.environ.get("RUN_HANDLER_FACTORY", "app.generation.artifacts:default_handlers"),
        operation_timeout_seconds=float(os.environ.get("OPERATION_TIMEOUT_SECONDS", "120")),
        operation_max_calls=_int("OPERATION_MAX_CALLS", 2),
        operation_token_budget=_int("OPERATION_TOKEN_BUDGET", 16_000),
        conversation_context_max_bytes=_int(
            "CONVERSATION_CONTEXT_MAX_BYTES", context_max_default),
        llm_context_window_tokens=context_window,
        # The local 4k model cannot afford a handoff prompt by default; cloud
        # deployments opt in (the Azure Bicep sets this true).
        conversation_compaction_enabled=os.environ.get(
            "CONVERSATION_COMPACTION_ENABLED",
            "false" if provider == "hf-local" else "true",
        ).lower() == "true",
        conversation_compaction_threshold=float(
            os.environ.get("CONVERSATION_COMPACTION_THRESHOLD", "0.5")),
        conversation_compaction_keep_turns=_int("CONVERSATION_COMPACTION_KEEP_TURNS", 4),
        conversation_compaction_max_output_tokens=compaction_output,
        conversation_compaction_timeout_seconds=float(
            os.environ.get("CONVERSATION_COMPACTION_TIMEOUT_SECONDS", "180")),
        conversation_compaction_token_budget=compaction_budget,
        conversation_token_estimate_bytes=bytes_per_token,
        batch_operation_timeout_seconds=float(os.environ.get("BATCH_OPERATION_TIMEOUT_SECONDS", "172800")),
        llm_sampling_policy=os.environ.get("LLM_SAMPLING_POLICY", "greedy"),
        llm_reasoning_effort=os.environ.get("LLM_REASONING_EFFORT") or None,
        llm_compatible_thinking=os.environ.get("LLM_COMPATIBLE_THINKING") or None,
        llm_compatible_cache_key=os.environ.get("LLM_COMPATIBLE_CACHE_KEY", "false").lower() == "true",
        auth_mode=os.environ.get("AUTH_MODE", "development"),
        auth_server_key=os.environ.get("AUTH_SERVER_KEY") or None,
        oidc_issuer=os.environ.get("OIDC_ISSUER") or None,
        oidc_client_id=os.environ.get("OIDC_CLIENT_ID") or None,
        oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET") or None,
        oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI") or None,
        auth_membership_claim=os.environ.get("AUTH_MEMBERSHIP_CLAIM") or None,
        auth_membership_values=tuple(v.strip() for v in os.environ.get("AUTH_MEMBERSHIP_VALUES", "").split(",") if v.strip()),
        auth_verified_email_domains=tuple(v.strip() for v in os.environ.get("AUTH_VERIFIED_EMAIL_DOMAINS", "").split(",") if v.strip()),
        admin_bootstrap_emails=tuple(
            v.strip().lower() for v in os.environ.get("ADMIN_BOOTSTRAP_EMAILS", "").split(",")
            if v.strip()),
        admin_bootstrap_subjects=tuple(
            v.strip() for v in os.environ.get("ADMIN_BOOTSTRAP_SUBJECTS", "").split(",")
            if v.strip()),
        admin_panel_enabled=os.environ.get("ADMIN_PANEL_ENABLED", "true").lower() == "true",
        debug_panel_enabled=os.environ.get("DEBUG_PANEL_ENABLED", "true").lower() == "true",
        debug_session_ttl_seconds=_int("DEBUG_SESSION_TTL_SECONDS", 28800),
        auth_session_ttl_seconds=_int("AUTH_SESSION_TTL_SECONDS", 28800),
        auth_transaction_ttl_seconds=_int("AUTH_TRANSACTION_TTL_SECONDS", 300),
        tools_enabled=os.environ.get("TOOLS_ENABLED", "false").lower() == "true",
        answer_citation_repair_enabled=os.environ.get(
            "ANSWER_CITATION_REPAIR_ENABLED", "false").lower() == "true",
        tool_max_calls=_int("TOOL_MAX_CALLS", 4),
        query_planning_enabled=os.environ.get("QUERY_PLANNING_ENABLED", "false").lower() == "true",
        query_planning_token_budget=_int("QUERY_PLANNING_TOKEN_BUDGET", 300_000),
        advising_research_enabled=os.environ.get(
            "ADVISING_RESEARCH_ENABLED", "false").lower() == "true",
        research_max_calls=_int("RESEARCH_MAX_CALLS", 12),
        research_max_tool_calls=_int("RESEARCH_MAX_TOOL_CALLS", 24),
        research_operation_timeout_seconds=float(
            os.environ.get("RESEARCH_OPERATION_TIMEOUT_SECONDS", "240")),
        research_operation_token_budget=_int("RESEARCH_OPERATION_TOKEN_BUDGET", 640_000),
        research_max_prompt_bytes=_int("RESEARCH_MAX_PROMPT_BYTES", 48_000),
        research_max_output_tokens=_int("RESEARCH_MAX_OUTPUT_TOKENS", 4096),
        office_enabled=os.environ.get("OFFICE_ENABLED", "false").lower() == "true",
        sandbox_remote_url=os.environ.get("SANDBOX_REMOTE_URL") or None,
        sandbox_bearer_token=os.environ.get("SANDBOX_BEARER_TOKEN") or None,
        sandbox_office_image=os.environ.get("SANDBOX_OFFICE_IMAGE", "emory-sandbox-office:7.4.7-v1"),
        office_timeout_seconds=float(os.environ.get("OFFICE_TIMEOUT_SECONDS", "60")),
        office_max_input_bytes=_int("OFFICE_MAX_INPUT_BYTES", 16 * 1024 * 1024),
        office_max_output_bytes=_int("OFFICE_MAX_OUTPUT_BYTES", 32 * 1024 * 1024),
        db_pool_max_size=_int("DB_POOL_MAX_SIZE", 20),
        db_pool_timeout_seconds=float(os.environ.get("DB_POOL_TIMEOUT_SECONDS", "10")),
        db_statement_timeout_ms=_int("DB_STATEMENT_TIMEOUT_MS", 30_000),
        db_idle_transaction_timeout_ms=_int("DB_IDLE_TRANSACTION_TIMEOUT_MS", 60_000),
        http_threadpool_tokens=_int("HTTP_THREADPOOL_TOKENS", 40),
        admission_max_pending_workspace=_int("ADMISSION_MAX_PENDING_WORKSPACE", 0),
        admission_max_pending_global=_int("ADMISSION_MAX_PENDING_GLOBAL", 0),

    )


def default_hf_stop_token_strings(model_id: str) -> tuple[str, ...]:
    """Return repository-owned stop-token defaults for a known local model."""
    return _DEFAULT_HF_STOP_TOKEN_STRINGS if model_id == DEFAULT_HF_MODEL_ID else ()


def with_hf_model(settings: Settings, model_id: str) -> Settings:
    """Return settings for a model override, resetting model-specific defaults."""
    if settings.hf_model_id == model_id:
        return settings
    from dataclasses import replace

    return replace(
        settings, hf_model_id=model_id,
        hf_stop_token_strings=default_hf_stop_token_strings(model_id),
    )


def configure_runtime_database(dsn: str) -> None:
    """Pass the live embedded database URL to the server/reloader process."""
    os.environ["DATABASE_URL"] = dsn
