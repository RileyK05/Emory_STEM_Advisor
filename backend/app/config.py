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


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    """Only "true" (any case) is true; any other set value is false."""
    return os.environ.get(name, "true" if default else "false").lower() == "true"


def _optional(name: str) -> str | None:
    """The variable's value, with unset and empty both meaning None."""
    return os.environ.get(name) or None


def _csv(name: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.environ.get(name, "").split(",") if v.strip())


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
    # Storage seam backend. `local` is the default and the only adapter wired
    # into production; `blob` and `dual` remain test seams until an Azure Blob
    # client is installed.
    storage_backend: str = "local"
    # Optional exact tokenizer tokens that terminate a local model turn.
    # None selects defaults for the configured model; () disables additions.
    hf_stop_token_strings: tuple[str, ...] | None = None
    llm_model_id: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)
    llm_base_url: str | None = None
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_extracted_chars: int = 2_000_000
    upload_max_segment_chars: int = 16_384
    upload_max_chunks: int = 300
    upload_embed_batch_size: int = 64
    upload_index_timeout_seconds: float = 60.0
    # Per-workspace byte ceiling across private source uploads and saved
    # artifact revisions/originals. 100 MiB (5x the 20 MiB single-upload cap)
    # fits dozens of syllabi or transcripts plus hundreds of advising plans
    # while bounding disk and JSONB growth per student (500 x 16 MB specs was
    # up to 8 GB before this cap).
    workspace_storage_max_bytes: int = 100 * 1024 * 1024
    request_max_bytes: int = 21 * 1024 * 1024
    readiness_timeout_seconds: int = 3
    # Optional HTTP liveness/readiness port for scripts.worker; 0 disables it.
    worker_health_port: int = 0
    embedding_device: str = "cpu"
    # Torch threads for CPU embedding; 0 keeps torch's default (every core). A
    # worker running several job threads sets cores // concurrency so they do
    # not oversubscribe the CPU.
    embedding_threads: int = 0
    # Opt-in: load the embedding model in a daemon thread after the app is
    # constructed, so the first real query does not pay the model-load cost.
    embedder_warmup_on_startup: bool = False
    profile_encryption_key: str | None = field(default=None, repr=False)
    llm_timeout_seconds: float = 60.0
    # Per-process, per-provider cap on model calls in flight. Callers wait up to
    # the operation's remaining time rather than failing immediately at the cap.
    llm_max_concurrency: int = 8
    llm_max_output_tokens: int = 1024
    # A native artifact spec (a full plan or workbook) is far longer than an answer:
    # a printable four-semester plan ran out at 4,096 tokens. At about 70 tokens a
    # second, 8,192 still fits a 120-second call.
    artifact_max_output_tokens: int = 8_192
    routing_enabled: bool = False
    routing_eval_suite_hash: str | None = None
    admin_api_enabled: bool = False
    # Institutional Admin and Debug surfaces. Both still require an admin
    # principal; these flags are deployment kill switches, not authorization.
    admin_panel_enabled: bool = True
    debug_panel_enabled: bool = True
    debug_session_ttl_seconds: int = 28800

    # One artifact run is one operation sized for the checked call chain:
    # outline, one call per section, in-conversation corrections and repair rounds.
    artifact_max_sections: int = 8
    artifact_repair_attempts: int = 2
    artifact_repair_rounds: int = 2
    # Sections written at once. Each runs on its own connection; the provider's
    # LLM_MAX_CONCURRENCY gate still bounds calls in flight.
    artifact_section_concurrency: int = 4
    artifact_operation_timeout_seconds: float = 300.0
    artifact_operation_max_calls: int = 40
    # Starting ceiling until R0's measured p95 plus 25% is recorded.
    artifact_operation_token_budget: int = 300_000
    # The hard budget for a turn's saved transcript plus referenced artifact
    # revisions. Research itself reads at most 6,000 bytes of transcript.
    conversation_context_max_bytes: int = 512_000
    # Optional visual feedback for HTML artifacts: render the draft, send the
    # screenshot to a vision-capable profile, and allow one bounded correction.
    visual_artifact_review_enabled: bool = False
    # The statically configured provider accepts images. When routing is
    # enabled, the reviewer is chosen by the vision capability instead.
    llm_vision: bool = False
    screenshot_renderer: str = "disabled"  # disabled | playwright
    screenshot_timeout_seconds: int = 30
    screenshot_width: int = 1280
    screenshot_height: int = 800
    screenshot_max_bytes: int = 8 * 1024 * 1024

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
    tools_enabled: bool = True
    # One research run gets exactly one operation with these limits.
    # Reservation units are serialized prompt bytes, so the token budget is an
    # accounting ceiling, not a promised token bill. The budgets are the p95 plus
    # 25%, rounded, of 800 settled research-43 turns on minimax-m3.1-flash
    # (2026-11-08): model calls p50 4, p95 13; tool calls p50 2, p95 11; tokens
    # p50 44,354, p95 165,843. A run that spends its budget still answers from
    # what it read, or settles with the fixed retry message.
    research_max_calls: int = 16
    research_max_tool_calls: int = 14
    # Effort a research round asks of a provider that supports it, unless the
    # connection sets its own; empty sends none. Measured 2026-11-04 on 12 live
    # questions: medium cut the median run from 213 s to 80 s and kept the facts low
    # dropped. Research alone gives up greedy reproducibility for this.
    research_reasoning_effort: str | None = "medium"
    research_operation_timeout_seconds: float = 360.0
    research_operation_token_budget: int = 210_000
    research_max_prompt_bytes: int = 52_000
    # Reasoning models spend most of this on hidden deliberation: a structured
    # state update or final answer routinely takes 5,000 to 9,000 tokens.
    research_max_output_tokens: int = 10_240
    # A reply cut off at the output limit is retried with a doubled allowance up
    # to this ceiling: reasoning models spend hidden tokens before any tool call.
    research_truncation_max_output_tokens: int = 16_384
    audit_max_search_states: int = 50_000
    office_enabled: bool = False
    sandbox_remote_url: str | None = None
    sandbox_bearer_token: str | None = field(default=None, repr=False)
    sandbox_office_image: str = "emory-sandbox-office:7.4.7-v1"
    sandbox_screenshot_image: str = "emory-sandbox-browser:1.50.0-v1"
    office_timeout_seconds: float = 60.0
    office_max_input_bytes: int = 16 * 1024 * 1024
    office_max_output_bytes: int = 32 * 1024 * 1024
    # Limit per pool role per process; conversation workers use two roles.
    # 0 disables pooling.
    db_pool_max_size: int = 20
    # Replicas of this process kind expected at peak, used by the startup
    # connection-budget preflight. Set it to the larger of the API and worker
    # replica counts in a deployment; the default assumes one of each.
    db_expected_replicas: int = 1
    db_pool_timeout_seconds: float = 10.0
    # Server-side limits for pooled application connections, so a slow plan or a
    # request that leaves a transaction open cannot pin a slot indefinitely.
    # 0 disables the corresponding server setting.
    db_statement_timeout_ms: int = 30_000
    db_idle_transaction_timeout_ms: int = 60_000
    # A session-capable URL for the same database. Set it to a different URL
    # when DATABASE_URL points at a transaction-mode pooler (PgBouncer):
    # LISTEN, migrations, backups and readiness probes then use this URL.
    # Connections through the pooler skip server-side prepared statements and
    # per-connection startup options, so the two timeouts above must also be
    # server parameters there. Unset, or equal to DATABASE_URL, means no pooler.
    database_direct_url: str | None = None
    # Sync FastAPI dependencies and handlers share one AnyIO thread pool per
    # process. Size it for expected concurrent requests so a dependency waiting
    # for a pooled connection cannot starve the handler that releases it.
    http_threadpool_tokens: int = 40
    # Opt-in admission caps for outstanding conversation turns. 0 disables the
    # corresponding cap. The check and the job insert share one transaction and
    # an advisory lock, so the cap holds across replicas.
    admission_max_pending_workspace: int = 0
    admission_max_pending_global: int = 0
    # Per-student quota limits; 0 means unlimited and is the default.
    quota_queries_per_minute: int = 0
    quota_queries_per_day: int = 0
    quota_tokens_per_day: int = 0
    quota_spend_usd_per_day: float = 0.0
    quota_max_active_jobs: int = 0

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
        if type(self.worker_health_port) is not int or not 0 <= self.worker_health_port <= 65535:
            raise ValueError("WORKER_HEALTH_PORT must be between 0 (disabled) and 65535")
        if type(self.db_pool_max_size) is not int or not 0 <= self.db_pool_max_size <= 200:
            raise ValueError("DB_POOL_MAX_SIZE must be between 0 (disabled) and 200")
        if type(self.db_expected_replicas) is not int or not 1 <= self.db_expected_replicas <= 1000:
            raise ValueError("DB_EXPECTED_REPLICAS must be between 1 and 1000")
        if not math.isfinite(self.db_pool_timeout_seconds) or not 0.1 <= self.db_pool_timeout_seconds <= 120:
            raise ValueError("DB_POOL_TIMEOUT_SECONDS must be between 0.1 and 120")
        if type(self.db_statement_timeout_ms) is not int or not 0 <= self.db_statement_timeout_ms <= 600_000:
            raise ValueError("DB_STATEMENT_TIMEOUT_MS must be between 0 (disabled) and 600000")
        if (type(self.db_idle_transaction_timeout_ms) is not int
                or not 0 <= self.db_idle_transaction_timeout_ms <= 3_600_000):
            raise ValueError(
                "DB_IDLE_TRANSACTION_TIMEOUT_MS must be between 0 (disabled) and 3600000")
        if self.database_direct_url is not None and not self.database_url:
            raise ValueError("DATABASE_DIRECT_URL needs DATABASE_URL (the pooled URL) as well")
        if type(self.http_threadpool_tokens) is not int or not 1 <= self.http_threadpool_tokens <= 1000:
            raise ValueError("HTTP_THREADPOOL_TOKENS must be between 1 and 1000")
        for name, value in (
            ("ADMISSION_MAX_PENDING_WORKSPACE", self.admission_max_pending_workspace),
            ("ADMISSION_MAX_PENDING_GLOBAL", self.admission_max_pending_global),
        ):
            if type(value) is not int or not 0 <= value <= 100_000:
                raise ValueError(f"{name} must be between 0 (disabled) and 100000")
        for name, value in (
            ("QUOTA_QUERIES_PER_MINUTE", self.quota_queries_per_minute),
            ("QUOTA_QUERIES_PER_DAY", self.quota_queries_per_day),
            ("QUOTA_TOKENS_PER_DAY", self.quota_tokens_per_day),
            ("QUOTA_MAX_ACTIVE_JOBS", self.quota_max_active_jobs),
        ):
            if type(value) is not int or not 0 <= value <= 2_147_483_647:
                raise ValueError(f"{name} must be between 0 (unlimited) and 2147483647")
        if not math.isfinite(self.quota_spend_usd_per_day) or self.quota_spend_usd_per_day < 0:
            raise ValueError("QUOTA_SPEND_USD_PER_DAY must be finite and nonnegative")
        if not 1024 <= self.conversation_context_max_bytes <= 1_000_000:
            raise ValueError("CONVERSATION_CONTEXT_MAX_BYTES must be between 1024 and 1000000")
        if self.llm_sampling_policy not in {"greedy", "model_default"}:
            raise ValueError("LLM_SAMPLING_POLICY must be greedy or model_default")
        if self.llm_reasoning_effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("LLM_REASONING_EFFORT is unsupported")
        if self.research_reasoning_effort not in {None, "none", "minimal", "low", "medium", "high",
                                                   "xhigh", "max"}:
            raise ValueError("RESEARCH_REASONING_EFFORT is unsupported")
        if self.llm_compatible_thinking not in {None, "enabled", "disabled"}:
            raise ValueError("LLM_COMPATIBLE_THINKING must be enabled or disabled")
        if self.llm_sampling_policy == "greedy" and self.llm_reasoning_effort not in {None, "none"}:
            raise ValueError("reasoning effort requires explicit model_default sampling policy")
        if self.llm_provider == "hf-local" and (self.llm_sampling_policy != "greedy"
                or self.llm_reasoning_effort is not None):
            raise ValueError("hf-local requires greedy generation without reasoning effort")
        if not 1 <= self.research_max_calls <= 100:
            raise ValueError("RESEARCH_MAX_CALLS must be between 1 and 100")
        if not 1 <= self.audit_max_search_states <= 1_000_000:
            raise ValueError("AUDIT_MAX_SEARCH_STATES must be between 1 and 1000000")
        if not 1 <= self.research_max_tool_calls <= 100:
            raise ValueError("RESEARCH_MAX_TOOL_CALLS must be between 1 and 100")
        if (not math.isfinite(self.research_operation_timeout_seconds)
                or self.research_operation_timeout_seconds <= 0
                or not 1_000 <= self.research_operation_token_budget <= 20_000_000
                or not 1_024 <= self.research_max_prompt_bytes <= 8_000_000
                or self.research_max_output_tokens <= 0
                or self.research_truncation_max_output_tokens < self.research_max_output_tokens):
            raise ValueError("research limits are invalid")
        if (not 1 <= self.artifact_max_sections <= 50
                or not 0 <= self.artifact_repair_attempts <= 10
                or not 0 <= self.artifact_repair_rounds <= 10
                or not 1 <= self.artifact_section_concurrency <= 16):
            raise ValueError("artifact section and repair limits are invalid")
        if self.auth_mode not in {"development", "oidc"}:
            raise ValueError("AUTH_MODE must be development or oidc")
        if self.storage_backend not in {"local", "blob", "dual"}:
            raise ValueError("STORAGE_BACKEND must be local, blob or dual")
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
        if (type(self.workspace_storage_max_bytes) is not int
                or not 1 <= self.workspace_storage_max_bytes <= 100 * 1024 * 1024 * 1024):
            raise ValueError("WORKSPACE_STORAGE_MAX_BYTES must be between 1 and 107374182400")
        if (min(self.upload_max_extracted_chars, self.upload_max_segment_chars,
                self.upload_max_chunks, self.upload_embed_batch_size) <= 0
                or not math.isfinite(self.upload_index_timeout_seconds)
                or self.upload_index_timeout_seconds <= 0):
            raise ValueError("upload indexing limits must be positive")
        if self.embedding_max_tokens <= 0 or self.hf_max_context <= 0:
            raise ValueError("model token ceilings must be positive")
        if (not math.isfinite(self.llm_timeout_seconds)
                or self.llm_timeout_seconds <= 0 or self.llm_max_output_tokens <= 0
                or self.artifact_max_output_tokens <= 0):
            raise ValueError("generation limits must be positive")
        if type(self.llm_max_concurrency) is not int or not 1 <= self.llm_max_concurrency <= 1000:
            raise ValueError("LLM_MAX_CONCURRENCY must be between 1 and 1000")
        if (not math.isfinite(self.artifact_operation_timeout_seconds)
                or self.artifact_operation_timeout_seconds <= 0
                or not 1 <= self.artifact_operation_max_calls <= 100
                or self.artifact_operation_token_budget < 1):
            raise ValueError("artifact operation limits are invalid")
        if self.screenshot_renderer not in {"disabled", "sandbox", "playwright"}:
            raise ValueError("SCREENSHOT_RENDERER must be disabled, sandbox, or playwright")
        if self.screenshot_renderer == "sandbox" and not (
            self.sandbox_remote_url and self.sandbox_bearer_token
        ):
            raise ValueError("sandbox screenshots require an authenticated remote sandbox")
        if (not 1 <= self.screenshot_timeout_seconds <= 120
                or not 320 <= self.screenshot_width <= 3840
                or not 240 <= self.screenshot_height <= 2160
                or not 1024 <= self.screenshot_max_bytes <= 32 * 1024 * 1024):
            raise ValueError("screenshot limits are invalid")


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
        database_url=_optional("DATABASE_URL"),
        default_collection=os.environ.get("DEFAULT_COLLECTION", "default"),
        data_dir=data_dir,
        storage_backend=os.environ.get("STORAGE_BACKEND", "local").lower(),
        llm_model_id=_optional("LLM_MODEL_ID"),
        llm_api_key=os.environ.get("LLM_API_KEY") or (os.environ.get(key_variable) if key_variable else None),
        llm_base_url=_optional("LLM_BASE_URL"),
        upload_max_bytes=_int("UPLOAD_MAX_BYTES", 20 * 1024 * 1024),
        upload_max_extracted_chars=_int("UPLOAD_MAX_EXTRACTED_CHARS", 2_000_000),
        upload_max_segment_chars=_int("UPLOAD_MAX_SEGMENT_CHARS", 16_384),
        upload_max_chunks=_int("UPLOAD_MAX_CHUNKS", 300),
        upload_embed_batch_size=_int("UPLOAD_EMBED_BATCH_SIZE", 64),
        upload_index_timeout_seconds=_float("UPLOAD_INDEX_TIMEOUT_SECONDS", 60),
        workspace_storage_max_bytes=_int("WORKSPACE_STORAGE_MAX_BYTES", 100 * 1024 * 1024),
        embedding_device=os.environ.get("EMBEDDING_DEVICE", "cpu"),
        embedding_threads=_int("EMBEDDING_THREADS", 0),
        embedder_warmup_on_startup=_bool("EMBEDDER_WARMUP_ON_STARTUP", False),
        request_max_bytes=_int("REQUEST_MAX_BYTES", 21 * 1024 * 1024),
        readiness_timeout_seconds=_int("READINESS_TIMEOUT_SECONDS", 3),
        worker_health_port=_int("WORKER_HEALTH_PORT", 0),
        profile_encryption_key=_optional("PROFILE_ENCRYPTION_KEY"),
        llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 60),
        llm_max_concurrency=_int("LLM_MAX_CONCURRENCY", 8),
        llm_max_output_tokens=_int("LLM_MAX_OUTPUT_TOKENS", 1024),
        artifact_max_output_tokens=_int("ARTIFACT_MAX_OUTPUT_TOKENS", 8_192),
        routing_enabled=_bool("ROUTING_ENABLED", False),
        routing_eval_suite_hash=_optional("ROUTING_EVAL_SUITE_HASH"),
        admin_api_enabled=_bool("ADMIN_API_ENABLED", False),
        artifact_max_sections=_int("ARTIFACT_MAX_SECTIONS", 8),
        artifact_repair_attempts=_int("ARTIFACT_REPAIR_ATTEMPTS", 2),
        artifact_repair_rounds=_int("ARTIFACT_REPAIR_ROUNDS", 2),
        artifact_section_concurrency=_int("ARTIFACT_SECTION_CONCURRENCY", 4),
        artifact_operation_timeout_seconds=_float("ARTIFACT_OPERATION_TIMEOUT_SECONDS", 300),
        artifact_operation_max_calls=_int("ARTIFACT_OPERATION_MAX_CALLS", 40),
        artifact_operation_token_budget=_int("ARTIFACT_OPERATION_TOKEN_BUDGET", 300_000),
        conversation_context_max_bytes=_int("CONVERSATION_CONTEXT_MAX_BYTES", 512_000),
        visual_artifact_review_enabled=_bool("VISUAL_ARTIFACT_REVIEW_ENABLED", False),
        llm_vision=_bool("LLM_VISION", False),
        screenshot_renderer=os.environ.get("SCREENSHOT_RENDERER", "disabled"),
        screenshot_timeout_seconds=_int("SCREENSHOT_TIMEOUT_SECONDS", 30),
        screenshot_width=_int("SCREENSHOT_WIDTH", 1280),
        screenshot_height=_int("SCREENSHOT_HEIGHT", 800),
        screenshot_max_bytes=_int("SCREENSHOT_MAX_BYTES", 8 * 1024 * 1024),
        llm_sampling_policy=os.environ.get("LLM_SAMPLING_POLICY", "greedy"),
        llm_reasoning_effort=_optional("LLM_REASONING_EFFORT"),
        llm_compatible_thinking=_optional("LLM_COMPATIBLE_THINKING"),
        llm_compatible_cache_key=_bool("LLM_COMPATIBLE_CACHE_KEY", False),
        auth_mode=os.environ.get("AUTH_MODE", "development"),
        auth_server_key=_optional("AUTH_SERVER_KEY"),
        oidc_issuer=_optional("OIDC_ISSUER"),
        oidc_client_id=_optional("OIDC_CLIENT_ID"),
        oidc_client_secret=_optional("OIDC_CLIENT_SECRET"),
        oidc_redirect_uri=_optional("OIDC_REDIRECT_URI"),
        auth_membership_claim=_optional("AUTH_MEMBERSHIP_CLAIM"),
        auth_membership_values=_csv("AUTH_MEMBERSHIP_VALUES"),
        auth_verified_email_domains=_csv("AUTH_VERIFIED_EMAIL_DOMAINS"),
        admin_bootstrap_emails=tuple(v.lower() for v in _csv("ADMIN_BOOTSTRAP_EMAILS")),
        admin_bootstrap_subjects=_csv("ADMIN_BOOTSTRAP_SUBJECTS"),
        admin_panel_enabled=_bool("ADMIN_PANEL_ENABLED", True),
        debug_panel_enabled=_bool("DEBUG_PANEL_ENABLED", True),
        debug_session_ttl_seconds=_int("DEBUG_SESSION_TTL_SECONDS", 28800),
        auth_session_ttl_seconds=_int("AUTH_SESSION_TTL_SECONDS", 28800),
        auth_transaction_ttl_seconds=_int("AUTH_TRANSACTION_TTL_SECONDS", 300),
        tools_enabled=_bool("TOOLS_ENABLED", True),
        research_max_calls=_int("RESEARCH_MAX_CALLS", 16),
        research_max_tool_calls=_int("RESEARCH_MAX_TOOL_CALLS", 14),
        research_reasoning_effort=os.environ.get("RESEARCH_REASONING_EFFORT", "medium") or None,
        research_operation_timeout_seconds=_float("RESEARCH_OPERATION_TIMEOUT_SECONDS", 360),
        research_operation_token_budget=_int("RESEARCH_OPERATION_TOKEN_BUDGET", 210_000),
        research_max_prompt_bytes=_int("RESEARCH_MAX_PROMPT_BYTES", 52_000),
        research_max_output_tokens=_int("RESEARCH_MAX_OUTPUT_TOKENS", 10_240),
        research_truncation_max_output_tokens=_int("RESEARCH_TRUNCATION_MAX_OUTPUT_TOKENS", 16_384),
        audit_max_search_states=_int("AUDIT_MAX_SEARCH_STATES", 50_000),
        office_enabled=_bool("OFFICE_ENABLED", False),
        sandbox_remote_url=_optional("SANDBOX_REMOTE_URL"),
        sandbox_bearer_token=_optional("SANDBOX_BEARER_TOKEN"),
        sandbox_office_image=os.environ.get("SANDBOX_OFFICE_IMAGE", "emory-sandbox-office:7.4.7-v1"),
        sandbox_screenshot_image=os.environ.get(
            "SANDBOX_SCREENSHOT_IMAGE", "emory-sandbox-browser:1.50.0-v1"),
        office_timeout_seconds=_float("OFFICE_TIMEOUT_SECONDS", 60),
        office_max_input_bytes=_int("OFFICE_MAX_INPUT_BYTES", 16 * 1024 * 1024),
        office_max_output_bytes=_int("OFFICE_MAX_OUTPUT_BYTES", 32 * 1024 * 1024),
        db_pool_max_size=_int("DB_POOL_MAX_SIZE", 20),
        db_expected_replicas=_int("DB_EXPECTED_REPLICAS", 1),
        db_pool_timeout_seconds=_float("DB_POOL_TIMEOUT_SECONDS", 10),
        db_statement_timeout_ms=_int("DB_STATEMENT_TIMEOUT_MS", 30_000),
        db_idle_transaction_timeout_ms=_int("DB_IDLE_TRANSACTION_TIMEOUT_MS", 60_000),
        database_direct_url=_optional("DATABASE_DIRECT_URL"),
        http_threadpool_tokens=_int("HTTP_THREADPOOL_TOKENS", 40),
        admission_max_pending_workspace=_int("ADMISSION_MAX_PENDING_WORKSPACE", 0),
        admission_max_pending_global=_int("ADMISSION_MAX_PENDING_GLOBAL", 0),
        quota_queries_per_minute=_int("QUOTA_QUERIES_PER_MINUTE", 0),
        quota_queries_per_day=_int("QUOTA_QUERIES_PER_DAY", 0),
        quota_tokens_per_day=_int("QUOTA_TOKENS_PER_DAY", 0),
        quota_spend_usd_per_day=_float("QUOTA_SPEND_USD_PER_DAY", 0.0),
        quota_max_active_jobs=_int("QUOTA_MAX_ACTIVE_JOBS", 0),
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
