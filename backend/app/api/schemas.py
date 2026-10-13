"""HTTP request/response schemas. Field names mirror frontend/src/api/types.ts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20000)
    collectionId: str | None = None
    sourceIds: list[UUID] | None = Field(default=None, max_length=500)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must contain non-whitespace text")
        return value


class SeamStatus(BaseModel):
    name: Literal["keyword", "embed", "prereq", "plan"]
    enabled: bool
    state: Literal["active", "dormant", "disabled", "failed"]
    reason: str | None = None
    candidateCount: int


class Contribution(BaseModel):
    chunkId: str
    seams: list[str]
    normalizedScore: float
    sourceSlot: int


class ContextItem(BaseModel):
    chunkId: str
    sourceId: str
    sourceName: str
    locatorLabel: str
    text: str


class PromptInfo(BaseModel):
    system: str
    user: str


class ModelCallInfo(BaseModel):
    provider: str
    modelId: str
    promptTokens: int | None
    completionTokens: int | None
    usageSource: Literal["provider", "estimated", "unknown"] = "unknown"
    latencyMs: int


class RetrievalTrace(BaseModel):
    traceId: str
    query: str
    normalizedQuery: str
    seams: list[SeamStatus]
    contributions: list[Contribution]
    contextBlock: list[ContextItem]
    prompt: PromptInfo
    modelCall: ModelCallInfo
    warnings: list[str]
    errors: list[str]


class Citation(BaseModel):
    sourceId: str
    sourceName: str
    locatorLabel: str
    chunkId: str


class ClaimReviewUnit(BaseModel):
    text: str
    citationChunkIds: list[str]
    reviewStatus: Literal["unreviewed"] = "unreviewed"


class ResearchBlock(BaseModel):
    kind: Literal["fact", "judgment", "assumption", "limitation", "question"]
    text: str
    evidenceIds: list[str]
    citationNumbers: list[int]
    candidateIds: list[str]


class ResearchDecisionInfo(BaseModel):
    runId: str
    outcome: Literal["recommendation", "information", "clarification", "insufficient_evidence"]
    completeness: Literal["complete", "partial"]
    blocks: list[ResearchBlock]
    unresolvedQuestions: list[str]


class AnswerResponse(BaseModel):
    type: Literal["answer"] = "answer"
    answer: str
    citations: list[Citation]
    trace: RetrievalTrace
    claims: list[ClaimReviewUnit] = Field(default_factory=list)
    research: ResearchDecisionInfo | None = None


class RefusalResponse(BaseModel):
    type: Literal["refusal"] = "refusal"
    reason: str
    trace: RetrievalTrace


class SourceDoc(BaseModel):
    id: str
    name: str
    locatorType: str
    status: Literal["uploaded", "parsed", "chunked", "indexed", "failed"]
    chunkCount: int
    errorMessage: str | None = None
    warnings: list[str] = Field(default_factory=list)
    indexRevision: int = 0
    canReindex: bool = False


class CollectionDoc(BaseModel):
    id: str
    name: str


class CourseDoc(BaseModel):
    courseId: str
    code: str
    title: str


class RecommendationDoc(BaseModel):
    clusterId: str
    path: list[str]
    courses: list[CourseDoc]


class TopicGraphNode(BaseModel):
    id: str
    kind: Literal["root", "department", "topic", "course"]
    label: str
    departmentKey: str | None
    courseId: str | None = None
    code: str | None = None
    title: str | None = None


class TopicGraphEdge(BaseModel):
    parentId: str
    childId: str
    kind: Literal["contains"]


class SubgraphView(BaseModel):
    nodes: list[TopicGraphNode]
    edges: list[TopicGraphEdge]


# --- Response models for dict-returning routes -------------------------------
# These document and validate the wire shape. Handlers omit keys that do not
# apply, so routes using them set `response_model_exclude_unset=True` (absent
# stays absent, an explicit null stays null), and `extra="allow"` keeps any
# field a handler adds without silently dropping it from the response.


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class HealthStatus(_WireModel):
    status: Literal["ok"]


class ArtifactRevisionReference(_WireModel):
    artifactId: str
    revisionId: str


_RunState = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class ArtifactRun(_WireModel):
    runId: str
    state: _RunState
    artifactIds: list[str] | None = None
    artifactRevisions: list[ArtifactRevisionReference] | None = None
    error: str | None = None


class ArtifactRunEvent(_WireModel):
    eventId: int
    runId: str
    state: _RunState
    stage: str | None = None
    unit: str | None = None
    requestedOutputTokenLimit: int | None = None
    outputTokenLimit: int | None = None
    artifactIds: list[str] | None = None
    artifactRevisions: list[ArtifactRevisionReference] | None = None
    error: str | None = None


class ArtifactRunLimits(_WireModel):
    maxCalls: int
    totalTokenBudget: int
    perCallOutputCap: int


_ArtifactKind = Literal["document", "workbook", "html"]
_ArtifactStatus = Literal["ready", "needs_recalculation"]


class NativeArtifactSummary(_WireModel):
    artifactId: str
    revisionId: str
    kind: _ArtifactKind
    title: str
    status: _ArtifactStatus | None = None


class ArtifactOriginal(_WireModel):
    filename: str
    sha256: str
    preserved: bool


class NativeArtifactRecord(NativeArtifactSummary):
    parentRevisionId: str | None
    spec: dict[str, Any]
    original: ArtifactOriginal | None = None
    message: str | None = None


class ArtifactRevisionSummary(_WireModel):
    revisionId: str
    parentRevisionId: str | None
    title: str
    createdAt: str


class ArtifactPreview(_WireModel):
    kind: _ArtifactKind
    title: str | None = None
    html: str | None = None
    status: _ArtifactStatus | None = None
    sheets: list[dict[str, Any]] | None = None
    formulas: list[Any] | None = None
    sandboxRequired: bool | None = None


_BatchState = Literal[
    "prepared", "submitting", "pending", "validating", "in_progress", "finalizing", "completed",
    "failed", "expired", "cancel_requested", "cancelling", "cancelled", "unknown",
]


class ArtifactBatchItem(_WireModel):
    itemId: str
    state: str
    publicationState: str
    artifactId: str | None
    revisionId: str | None
    errorCode: str | None


class ArtifactBatchSummary(_WireModel):
    batchId: str
    state: _BatchState
    cancelRequested: bool
    itemCount: int
    totalCount: int | None
    completedCount: int | None
    failedCount: int | None
    createdAt: str
    updatedAt: str


class ArtifactBatchRecord(_WireModel):
    batchId: str
    state: _BatchState
    cancelRequested: bool
    itemCount: int
    totalCount: int | None
    completedCount: int | None
    failedCount: int | None
    items: list[ArtifactBatchItem]
    createdAt: str | None = None
    updatedAt: str | None = None


_TurnStatus = Literal["input", "answer", "refusal", "history", "failed", "abandoned"]
_JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "unknown"]


class ConversationSummary(_WireModel):
    conversationId: str
    title: str
    lastSequence: int
    pendingRequestId: str | None
    createdAt: str
    updatedAt: str


class ConversationTurn(_WireModel):
    sequence: int
    requestId: str
    role: Literal["user", "assistant"]
    text: str
    status: _TurnStatus
    evidence: dict[str, Any]
    createdAt: str


class ConversationRecord(ConversationSummary):
    turns: list[ConversationTurn]


class ConversationSummaryCheckpoint(_WireModel):
    summaryId: str
    conversationId: str
    state: Literal["pending", "completed", "refused", "failed", "stale"]
    expectedSequence: int
    candidateCoveredThrough: int
    coveredThrough: int | None
    fingerprint: str
    text: str | None
    referenceMetadata: list[dict[str, Any]]
    failureCode: str | None
    profileId: str | None
    profileRevision: int | None
    createdAt: str
    updatedAt: str


class ConversationStreamStartResponse(_WireModel):
    conversationId: str
    requestId: str
    jobId: str | None
    state: _JobState


class ConversationStreamEventsResponse(_WireModel):
    events: list[dict[str, Any]]
    nextEventId: int
    requestState: Literal["pending", "completed", "failed", "abandoned"]
    jobState: _JobState
    done: bool


class NativeDecisionUsage(_WireModel):
    source: Literal["provider", "estimated", "unknown"]
    promptTokens: int | None
    completionTokens: int | None


class NativeDecision(_WireModel):
    decisionId: str
    state: Literal["pending", "completed", "refused", "invalid", "failed", "unknown", "abandoned"]
    prediction: bool | None
    probability: float | None
    confidence: float | None
    refused: bool
    usage: NativeDecisionUsage


class ConversationTurnResponse(_WireModel):
    """The envelope every turn outcome shares; answer, refusal, history, and
    terminal bodies carry their own fields beyond it (passed through unchanged)."""

    type: Literal["answer", "refusal", "history", "failed", "abandoned"]
    conversationId: str
    sequence: int


class AuthSession(_WireModel):
    mode: Literal["development", "oidc"]
    authenticated: bool
    workspaceKey: str | None = None
    displayName: str | None = None
    email: str | None = None
