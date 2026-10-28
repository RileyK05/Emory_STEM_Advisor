"""HTTP request/response schemas. Field names mirror frontend/src/api/types.ts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    collectionId: str | None = Field(default=None, max_length=200)
    sourceIds: list[UUID] | None = Field(default=None, max_length=500)
    studentCohort: str | None = Field(default=None, max_length=200)

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
    timings: dict[str, int]


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


class SourceApplicabilityItem(BaseModel):
    sourceId: str
    sourceName: str
    sourceCategory: str
    edition: str | None = None
    publishedAt: str | None = None
    isCurrent: bool
    isSuperseded: bool
    freshness: Literal["current", "aging", "stale", "unknown"]
    applicabilityStatus: Literal["reviewed_default", "historical", "unknown"]
    cohortCompatibility: Literal["compatible", "mismatch", "unknown"]
    summary: str


class ApplicabilityNotice(BaseModel):
    level: Literal["info", "warning", "conflict"]
    headline: str
    hasConflicts: bool
    conflicts: list[str]
    sources: list[SourceApplicabilityItem]
    declaredCohort: str | None = None


class AnswerResponse(BaseModel):
    type: Literal["answer"] = "answer"
    answer: str
    citations: list[Citation]
    trace: RetrievalTrace
    claims: list[ClaimReviewUnit] = Field(default_factory=list)
    research: ResearchDecisionInfo | None = None
    applicability: ApplicabilityNotice | None = None


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
    lifecycle: Literal["current", "retired"] = "current"


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


class ExportSummaryRequest(_WireModel):
    title: str | None = Field(default=None, max_length=500)
    throughSequence: int | None = Field(default=None, ge=0, le=2_147_483_647)
    artifactReferences: list[ArtifactRevisionReference] = Field(
        default_factory=list, max_length=100)


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
    contextEpoch: int = 0
    contextBoundarySequence: int = 0
    contextResetAt: str | None = None
    archivedAt: str | None = None
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
    contextEpoch: int = 0
    createdAt: str
    updatedAt: str


class ConversationMemoryInspection(_WireModel):
    conversationId: str
    contextEpoch: int
    contextBoundarySequence: int
    contextResetAt: str | None = None
    checkpoints: list[ConversationSummaryCheckpoint]
    researchNotebook: dict[str, Any] | None = None


class ConversationResetResponse(_WireModel):
    conversationId: str
    contextEpoch: int
    contextBoundarySequence: int
    contextResetAt: str
    message: str = "Conversation context memory has been reset. Prior turns remain in transcript history but are excluded from future model context."


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


class ClaimAssessmentUnit(_WireModel):
    ordinal: int
    answerStart: int
    answerEnd: int
    text: str
    coverage: str
    modelAssessment: str
    modelConfidence: float | None
    evidence: list[dict]
    humanVerified: bool


class ClaimReview(_WireModel):
    reviewId: str
    responseId: str
    state: Literal["pending", "completed", "refused", "invalid", "failed", "unknown", "abandoned"]
    reviewVersion: int
    profileRevision: int | None
    provider: str | None
    model: str | None
    usage: NativeDecisionUsage
    units: list[ClaimAssessmentUnit]


class ConversationTurnResponse(_WireModel):
    """The envelope every turn outcome shares; answer, refusal, history, and
    terminal bodies carry their own fields beyond it (passed through unchanged)."""

    type: Literal["answer", "refusal", "history", "failed", "abandoned"]
    conversationId: str
    sequence: int


class AuthSession(_WireModel):
    mode: Literal["development", "oidc"]
    authenticated: bool
    workspaceKey: str | None = Field(default=None, max_length=200)
    displayName: str | None = None
    email: str | None = None
    role: Literal["student", "advisor", "admin", "auditor"] | None = None


class AdminOverview(_WireModel):
    users: int
    admins: int
    disabledUsers: int
    activeSessions: int
    conversations: int
    turns: int
    summaries: int
    queuedJobs: int
    runningJobs: int
    failedJobs: int
    sources: int
    artifacts: int
    traces: int


class AdminUser(_WireModel):
    userId: UUID
    issuer: str
    subject: str
    workspaceKey: str
    displayName: str | None = None
    email: str | None = None
    role: Literal["student", "advisor", "admin", "auditor"]
    status: Literal["active", "disabled"]
    conversationCount: int
    activeSessions: int
    createdAt: str
    updatedAt: str
    lastSeenAt: str | None = None


class AdminUserUpdate(BaseModel):
    role: Literal["student", "advisor", "admin", "auditor"]
    status: Literal["active", "disabled"]


_GrantType = Literal["advisor_access", "support_access"]
_GrantResourceType = Literal["conversation", "plan", "all_records"]


class ResourceGrantCreate(BaseModel):
    grantType: _GrantType
    actorUserId: UUID
    targetWorkspaceKey: str
    resourceType: _GrantResourceType
    resourceId: str | None = None
    reason: str = Field(min_length=5, max_length=1000)
    expiresAt: str
    allowedActions: list[str] = Field(default_factory=lambda: ["read"])


class ResourceGrant(_WireModel):
    grantId: UUID
    grantType: _GrantType
    actorUserId: UUID
    targetWorkspaceKey: str
    resourceType: _GrantResourceType
    resourceId: str | None = None
    allowedActions: list[str]
    reason: str
    approvedByUserId: UUID
    expiresAt: str
    createdAt: str
    revokedAt: str | None = None
    revokedByUserId: UUID | None = None
    actorEmail: str | None = None
    actorDisplayName: str | None = None
    approvedByEmail: str | None = None
    approvedByDisplayName: str | None = None


class SupportGrantRequestCreate(BaseModel):
    targetWorkspaceKey: str = Field(min_length=1, max_length=200)
    resourceType: _GrantResourceType
    resourceId: str | None = None
    reason: str = Field(min_length=5, max_length=1000)
    expiresAt: str


class SupportGrantRequestUpdate(BaseModel):
    targetWorkspaceKey: str = Field(min_length=1, max_length=200)
    resourceType: _GrantResourceType
    resourceId: str | None = None
    reason: str = Field(min_length=5, max_length=1000)
    expiresAt: str
    expectedRevision: int = Field(ge=1)


class SupportGrantRequestDecision(BaseModel):
    expectedRevision: int = Field(ge=1)
    note: str | None = None


class SupportGrantRequest(_WireModel):
    requestId: UUID
    requesterUserId: UUID
    actorUserId: UUID
    targetWorkspaceKey: str
    resourceType: _GrantResourceType
    resourceId: str | None = None
    allowedActions: list[str]
    reason: str
    expiresAt: str
    status: Literal["pending", "approved", "rejected"]
    revision: int
    decisionNote: str | None = None
    decidedByUserId: UUID | None = None
    decidedAt: str | None = None
    grantId: UUID | None = None
    createdAt: str
    updatedAt: str
    actorEmail: str | None = None
    actorDisplayName: str | None = None


class GrantedResultStaging(_WireModel):
    stagingId: UUID
    grantId: UUID
    status: Literal["staged", "published", "withheld"]
    resourceId: str


_PrivacyStatus = Literal["queued", "running", "completed", "failed"]


class PrivacyExportResponse(_WireModel):
    exportId: UUID
    workspaceKey: str
    status: _PrivacyStatus
    manifest: dict[str, Any] | None = None
    downloadToken: str | None = None
    expiresAt: str
    createdAt: str
    finishedAt: str | None = None
    error: str | None = None


class PrivacyExportDownloadResponse(_WireModel):
    exportId: UUID
    workspaceKey: str
    status: _PrivacyStatus
    manifest: dict[str, Any]
    contentDigest: str
    expiresAt: str


class PrivacyDeletionRequest(BaseModel):
    confirmation: str = Field(max_length=200)
    reason: str = Field(default="user_request", max_length=500)


class PrivacyDeletionResponse(_WireModel):
    deletionId: UUID
    workspaceKey: str
    status: _PrivacyStatus
    deletedCounts: dict[str, Any]
    createdAt: str
    finishedAt: str | None = None
    error: str | None = None
    message: str = "All workspace-owned conversations, artifacts, and private data have been deleted. Tombstones have been recorded to prevent replay or restore."



class AdminAuditEntry(_WireModel):
    auditId: UUID
    actorUserId: UUID | None = None
    actorWorkspace: str
    action: str
    targetType: str
    targetId: str | None = None
    detail: dict[str, Any]
    createdAt: str
    actorEmail: str | None = None
    actorDisplayName: str | None = None


class AdminConversation(_WireModel):
    conversationId: UUID
    workspaceKey: str
    title: str
    lastSequence: int
    pendingRequestId: UUID | None = None
    turnCount: int
    summaryCount: int
    createdAt: str
    updatedAt: str


class AdminConversationTurn(_WireModel):
    sequence: int
    requestId: str
    role: Literal["user", "assistant"]
    text: str
    status: str
    evidence: dict[str, Any]
    createdAt: str


class AdminTranscript(_WireModel):
    conversation: AdminConversation
    turns: list[AdminConversationTurn]


class AdminJob(_WireModel):
    jobId: UUID
    kind: str
    scopeKey: str
    status: str
    attemptCount: int
    maxAttempts: int
    cancelRequested: bool
    error: dict[str, Any] | None = None
    createdAt: str
    updatedAt: str
    finishedAt: str | None = None


class AdminSource(_WireModel):
    sourceId: UUID
    collectionId: UUID
    filename: str
    contentType: str
    status: str
    workspaceKey: str | None = Field(default=None, max_length=200)
    indexRevision: int
    isCurrent: bool
    createdAt: str
    updatedAt: str


class DebugSetting(_WireModel):
    name: str
    label: str
    group: str
    type: Literal["int", "float", "bool", "str"]
    value: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str] | None = None


class DebugSettingsResponse(_WireModel):
    settings: list[DebugSetting]
    retrieval: list[DebugSetting]
    systemPrompt: str
    defaultSystemPrompt: str


class DebugSessionCreate(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    systemPrompt: str | None = Field(default=None, max_length=20000)


class DebugSessionUpdate(BaseModel):
    overrides: dict[str, Any] | None = None
    systemPrompt: str | None = Field(default=None, max_length=20000)


class DebugSession(_WireModel):
    debugSessionId: UUID
    expiresAt: str
    overrides: dict[str, Any]
    systemPrompt: str | None = None
    createdAt: str
    updatedAt: str


class DebugSessionCreated(DebugSession):
    token: str


class AdminRevokedSessions(_WireModel):
    revoked: int


class AdminUserSession(_WireModel):
    createdAt: str
    expiresAt: str
    revokedAt: str | None = None


class AdminArtifact(_WireModel):
    artifactId: UUID
    workspaceKey: str
    kind: str
    headRevisionId: UUID
    originalFilename: str | None = None
    revisionCount: int
    createdAt: str


class AdminCollection(_WireModel):
    collectionId: UUID
    name: str
    sourceCount: int
    createdAt: str


class AdminCollectionWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TelemetryUsageSummary(_WireModel):
    costCurrency: str | None = None
    costCurrencies: list[str] = Field(default_factory=list)
    totalCalls: int
    completedCalls: int
    failedCalls: int
    totalPromptTokens: int | None = None
    totalCompletionTokens: int | None = None
    totalCachedTokens: int | None = None
    cachedTokenShare: float | None = None
    callsReportingCache: int
    callsWithCacheHits: int
    cacheHitFraction: float | None = None
    cacheReportingCoverage: float | None = None
    totalCost: float | None = None
    measuredCost: float | None = None
    estimatedCost: float | None = None
    unknownCostCalls: int
    costCompleteness: Literal["complete", "partial", "unknown"]


class TelemetryUsageBreakdownRow(_WireModel):
    costCurrency: str | None = None
    costCurrencies: list[str] = Field(default_factory=list)
    bucket: str
    provider: str | None = None
    modelId: str | None = None
    totalCalls: int
    completedCalls: int
    failedCalls: int
    totalPromptTokens: int | None = None
    totalCompletionTokens: int | None = None
    totalCachedTokens: int | None = None
    cachedTokenShare: float | None = None
    callsReportingCache: int
    callsWithCacheHits: int
    cacheHitFraction: float | None = None
    cacheReportingCoverage: float | None = None
    totalCost: float | None = None
    measuredCost: float | None = None
    estimatedCost: float | None = None
    unknownCostCalls: int
    costCompleteness: Literal["complete", "partial", "unknown"]


class TelemetryUsageResponse(_WireModel):
    summary: TelemetryUsageSummary
    breakdown: list[TelemetryUsageBreakdownRow]


class ModelPriceScheduleCreate(BaseModel):
    billingMode: Literal["standard", "batch"] = "standard"
    provider: str = Field(min_length=1, max_length=100)
    modelId: str = Field(min_length=1, max_length=100)
    currency: str = Field(default="USD", min_length=1, max_length=10)
    inputRatePerMillion: float = Field(ge=0)
    cachedReadRatePerMillion: float = Field(default=0, ge=0)
    outputRatePerMillion: float = Field(ge=0)
    effectiveFrom: str
    effectiveTo: str | None = None
    provenance: str = Field(min_length=1, max_length=500)


class ModelPriceSchedule(_WireModel):
    billingMode: Literal["standard", "batch"]
    scheduleId: UUID
    provider: str
    modelId: str
    currency: str
    inputRatePerMillion: float
    cachedReadRatePerMillion: float
    outputRatePerMillion: float
    effectiveFrom: str
    effectiveTo: str | None = None
    provenance: str
    createdAt: str


class FeatureFlagSummary(_WireModel):
    flagName: str
    description: str
    envCeilingKey: str | None = None
    defaultEnabled: bool
    killSwitchActive: bool
    killReason: str | None = None
    overridesCount: int
    createdAt: str | None = None
    updatedAt: str | None = None


class FeatureFlagOverride(_WireModel):
    overrideId: UUID
    flagName: str
    scopeType: Literal["global", "cohort", "workspace"]
    scopeId: str
    enabled: bool
    reason: str | None = None
    actorUserId: UUID | None = None
    updatedAt: str | None = None


class FeatureFlagOverrideRequest(BaseModel):
    expectedUpdatedAt: str | None = None
    requireAbsent: bool = False
    scopeType: Literal["global", "cohort", "workspace"]
    scopeId: str = Field(min_length=1, max_length=100)
    enabled: bool
    reason: str | None = Field(default=None, max_length=500)


class FeatureFlagKillSwitchRequest(BaseModel):
    expectedUpdatedAt: str | None = None
    killSwitchActive: bool
    reason: str | None = Field(default=None, max_length=500)


class CohortSummary(_WireModel):
    cohortName: str
    memberCount: int


class CohortAssignmentRequest(BaseModel):
    userId: UUID


class CohortMember(_WireModel):
    userId: UUID
    displayName: str | None = None
    email: str | None = None
    role: Literal["student", "advisor", "admin", "auditor"]
    assignedAt: str


class CohortAssignmentResponse(_WireModel):
    status: Literal["assigned"]
    cohortName: str
    userId: UUID


class EffectiveFlagsResponse(_WireModel):
    flags: dict[str, bool]


class EffectiveQuotaResponse(_WireModel):
    remainingQueriesMinute: int
    remainingQueriesDay: int
    minuteQueryLimit: int
    dailyQueryLimit: int
    activeJobsLimit: int
    resetDaySeconds: int


class QuotaPolicySummary(_WireModel):
    policyId: UUID
    scopeType: Literal["global", "cohort", "workspace", "user"]
    scopeId: str
    queriesPerMinute: int | None = None
    queriesPerDay: int | None = None
    tokensPerDay: int | None = None
    spendUsdPerDay: float | None = None
    maxActiveJobs: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class QuotaPolicyUpsertRequest(BaseModel):
    scopeType: Literal["global", "cohort", "workspace", "user"]
    scopeId: str = Field(min_length=1, max_length=100)
    queriesPerMinute: int | None = Field(default=None, ge=0, le=2_147_483_647)
    queriesPerDay: int | None = Field(default=None, ge=0, le=2_147_483_647)
    tokensPerDay: int | None = Field(default=None, ge=0, le=2_147_483_647)
    spendUsdPerDay: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    maxActiveJobs: int | None = Field(default=None, ge=0, le=2_147_483_647)


class AlertStatusResponse(_WireModel):
    alertName: str
    status: Literal["firing", "resolved", "unknown"]
    severity: Literal["critical", "warning", "info"]
    currentValue: float | None
    threshold: float
    owner: str
    runbook: str
    description: str
    evaluatedAt: str


class SourceReviewProposal(_WireModel):
    proposalId: UUID
    sourceId: UUID
    expectedMetadataRevision: int
    proposedByUserId: UUID | None = None
    status: str
    sourceCategory: str
    classificationOrigin: str
    publisher: str | None = None
    sourceUrl: str | None = None
    edition: str | None = None
    geography: str | None = None
    occupationId: str | None = None
    publishedAt: str | None = None
    acquiredAt: str | None = None
    attribution: str | None = None
    title: str | None = None
    institution: str | None = None
    issuingUnit: str | None = None
    documentRoles: list[str] = Field(default_factory=list)
    applicability: dict[str, list[str]] = Field(default_factory=dict)
    applicabilityStatus: str
    lifecycleStatus: str
    replacementIntent: str | None = None
    reviewNotes: str | None = None
    rejectionReason: str | None = None
    reviewedByUserId: UUID | None = None
    reviewedAt: str | None = None
    createdAt: str
    updatedAt: str


class SourceLifecycleEvent(_WireModel):
    eventId: UUID
    sourceId: UUID
    eventType: str
    proposalId: UUID | None = None
    metadataRevision: int
    actorUserId: UUID | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class SourceLifecycleChangeResponse(_WireModel):
    sourceId: UUID
    lifecycleStatus: str
    metadataRevision: int


class SourceDependencies(_WireModel):
    programCount: int
    courseCount: int
    gapCount: int
    researchRunCount: int
    programs: list[dict[str, Any]] = Field(default_factory=list)


class SourceReviewDetailResponse(_WireModel):
    source: dict[str, Any]
    pendingProposal: SourceReviewProposal | None = None
    proposals: list[SourceReviewProposal] = Field(default_factory=list)
    lifecycleEvents: list[SourceLifecycleEvent] = Field(default_factory=list)
    dependencies: SourceDependencies


class SourceImpactPreviewResponse(_WireModel):
    sourceId: str
    filename: str
    programCount: int
    courseCount: int
    gapCount: int
    researchRunCount: int
    programs: list[dict[str, Any]] = Field(default_factory=list)


class SourceReviewProposalCreate(BaseModel):
    expectedMetadataRevision: int = Field(ge=1)
    sourceCategory: str
    publisher: str | None = None
    sourceUrl: str | None = None
    edition: str | None = None
    geography: str | None = None
    occupationId: str | None = None
    publishedAt: str | None = None
    acquiredAt: str | None = None
    attribution: str | None = None
    title: str | None = None
    institution: str | None = None
    issuingUnit: str | None = None
    documentRoles: list[str] = Field(default_factory=list)
    applicability: dict[str, list[str]] = Field(default_factory=dict)
    applicabilityStatus: str = "unknown"
    lifecycleStatus: str = "active"
    replacementIntent: str | None = None
    reviewNotes: str | None = None


class SourceReviewProposalReject(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class SourceLifecycleChangeRequest(BaseModel):
    action: Literal["activate", "deprecate", "archive"]
    expectedMetadataRevision: int = Field(ge=1)
    notes: str = ""


class SourceRevisionPassage(_WireModel):
    chunkId: UUID
    chunkIndex: int
    locatorLabel: str
    text: str
    textDigest: str


class SourceRevisionEvidenceResponse(_WireModel):
    sourceId: UUID
    indexRevision: int
    passages: list[SourceRevisionPassage] = Field(default_factory=list)


class ProgramRawItem(_WireModel):
    position: int
    section: str | None = None
    text: str
    choose: int | None = None
    mentions: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ProgramRuleReleaseSummary(_WireModel):
    releaseId: UUID
    programId: UUID
    collectionId: UUID
    sourceId: UUID
    sourceIndexRevision: int
    sourceSha256: str
    releaseVersion: int
    status: str
    rulesDigest: str
    coverageSummary: dict[str, Any] = Field(default_factory=dict)
    createdByUserId: UUID | None = None
    approvedByUserId: UUID | None = None
    approvedAt: str | None = None
    notes: str | None = None
    createdAt: str
    updatedAt: str


class ProgramRuleReleaseDetail(_WireModel):
    releaseId: UUID
    programId: UUID
    collectionId: UUID
    sourceId: UUID
    sourceIndexRevision: int
    sourceSha256: str
    releaseVersion: int
    status: str
    rulesTree: dict[str, Any]
    rulesDigest: str
    coverageSummary: dict[str, Any] = Field(default_factory=dict)
    createdByUserId: UUID | None = None
    approvedByUserId: UUID | None = None
    approvedAt: str | None = None
    notes: str | None = None
    createdAt: str
    updatedAt: str


class ProgramInspectionResponse(_WireModel):
    program: dict[str, Any]
    items: list[ProgramRawItem] = Field(default_factory=list)
    releases: list[ProgramRuleReleaseSummary] = Field(default_factory=list)
    currentRelease: ProgramRuleReleaseDetail | None = None


class ProgramRuleDraftCreate(BaseModel):
    rulesTree: dict[str, Any] | None = None
    notes: str = ""


class ProgramRuleReviewAction(BaseModel):
    notes: str = ""


class ProgramRuleRejectAction(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ProgramRuleDiffResponse(_WireModel):
    programId: str
    releaseIdA: str
    versionA: int
    releaseIdB: str
    versionB: int
    addedNodeIds: list[str] = Field(default_factory=list)
    removedNodeIds: list[str] = Field(default_factory=list)
    modifiedNodes: list[dict[str, Any]] = Field(default_factory=list)
    digestMatch: bool


class AcademicRecordSummary(_WireModel):
    recordId: UUID
    workspaceKey: str
    recordRevision: int
    createdAt: str
    updatedAt: str


class AcademicRecordAttemptItem(_WireModel):
    attemptId: UUID
    recordId: UUID
    workspaceKey: str
    institution: str
    submittedCourseCode: str
    resolvedCourseCode: str | None = None
    courseTitle: str | None = None
    term: str
    attemptedCredits: float
    earnedCredits: float
    grade: str | None = None
    status: str
    provenance: str
    isVerified: bool
    verificationMethod: str | None = None
    verifiedByUserId: UUID | None = None
    verifiedAt: str | None = None
    verificationNotes: str | None = None
    createdAt: str
    updatedAt: str


class AcademicRecordVerificationEventItem(_WireModel):
    eventId: UUID
    recordId: UUID
    attemptId: UUID | None = None
    eventType: str
    actorUserId: UUID | None = None
    actorRole: str
    details: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class AcademicRecordDetailResponse(_WireModel):
    record: AcademicRecordSummary
    attempts: list[AcademicRecordAttemptItem] = Field(default_factory=list)
    recentEvents: list[AcademicRecordVerificationEventItem] = Field(default_factory=list)


class AcademicRecordAttemptUpsertRequest(BaseModel):
    attemptId: UUID | None = None
    institution: str = "Emory University"
    submittedCourseCode: str = Field(min_length=1, max_length=50)
    resolvedCourseCode: str | None = None
    courseTitle: str | None = None
    term: str = Field(min_length=1, max_length=50)
    attemptedCredits: float = Field(default=3.0, ge=0.0, le=30.0)
    earnedCredits: float | None = Field(default=None, ge=0.0, le=30.0)
    grade: str | None = None
    status: str | None = None
    provenance: str = "student_manual"
    workspaceKey: str | None = Field(default=None, max_length=200)


class AcademicRecordVerifyAttemptRequest(BaseModel):
    method: Literal["manual_staff", "official_transcript", "sis_feed"] = "manual_staff"
    notes: str | None = None
    workspaceKey: str | None = Field(default=None, max_length=200)


class AcademicRecordImportPreviewRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    rawContent: str = Field(min_length=1, max_length=2_000_000)
    expectedRecordRevision: int = Field(ge=1, le=2_147_483_647)
    workspaceKey: str | None = Field(default=None, max_length=200)


class AcademicRecordImportBatchResponse(_WireModel):
    batchId: UUID
    recordId: UUID
    workspaceKey: str
    expectedRecordRevision: int
    status: str
    filename: str
    parsedRows: list[dict[str, Any]] = Field(default_factory=list)
    rowErrors: list[dict[str, Any]] = Field(default_factory=list)
    duplicateCandidates: list[dict[str, Any]] = Field(default_factory=list)
    previewDigest: str | None = None
    rowDecisions: list[str] = Field(default_factory=list)
    createdAt: str
    committedAt: str | None = None


class AcademicRecordImportCorrection(BaseModel):
    rowIndex: int = Field(ge=0)
    submittedCourseCode: str | None = None
    term: str | None = None
    grade: str | None = None
    courseTitle: str | None = None
    attemptedCredits: float | None = None
    earnedCredits: float | None = None
    status: str | None = None


class AcademicRecordImportUpdateRequest(BaseModel):
    expectedRecordRevision: int = Field(ge=1, le=2_147_483_647)
    decisions: list[Literal["select", "reject"]] | None = None
    corrections: list[AcademicRecordImportCorrection] = Field(default_factory=list)
    workspaceKey: str | None = Field(default=None, max_length=200)


class AcademicRecordImportCommitRequest(BaseModel):
    expectedRecordRevision: int = Field(ge=1, le=2_147_483_647)
    previewDigest: str = Field(min_length=64, max_length=64)
    workspaceKey: str | None = Field(default=None, max_length=200)


class AcademicRecordRevisionResponse(_WireModel):
    recordId: UUID
    revision: int
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class AcademicRecordImportCommitResponse(_WireModel):
    batchId: UUID
    status: str
    attemptsImported: int
    newRecordRevision: int


class DegreeAuditRuleEvaluationItem(_WireModel):
    evaluationId: UUID
    auditId: UUID
    nodeId: str
    parentNodeId: str | None = None
    nodeType: str
    title: str
    status: str
    allocatedAttemptIds: list[str] = Field(default_factory=list)
    allocatedCredits: float
    requiredCount: int | None = None
    satisfiedCount: int | None = None
    unresolvedReason: str | None = None
    rawPosition: int | None = None
    createdAt: str


class DegreeAuditRecordItem(_WireModel):
    auditId: UUID
    workspaceKey: str
    programId: UUID
    ruleReleaseId: UUID
    recordId: UUID
    recordRevision: int
    rulesDigest: str
    overallStatus: str
    isStale: bool
    completedCredits: float
    inProgressCredits: float
    unverifiedCredits: float
    resultsSummary: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    programTitle: str | None = None
    programKind: str | None = None
    createdAt: str


class DegreeAuditDetailResponse(_WireModel):
    audit: DegreeAuditRecordItem
    evaluations: list[DegreeAuditRuleEvaluationItem] = Field(default_factory=list)
    programTitle: str
    programKind: str
    coverageScope: Literal["program_audit"]


class AuditableProgramItem(_WireModel):
    programId: UUID
    title: str
    kind: str
    ruleReleaseId: UUID


class DegreeAuditRunRequest(BaseModel):
    programId: UUID
    workspaceKey: str | None = Field(default=None, max_length=200)


class ProgramComparisonMember(_WireModel):
    position: int
    programId: UUID
    programTitle: str
    ruleReleaseId: UUID
    auditId: UUID
    savedStatus: str
    savedCompletedCredits: float
    overlayStatus: str
    unresolvedCount: int


class ProgramComparisonResponse(_WireModel):
    comparisonId: UUID
    workspaceKey: str
    recordId: UUID
    recordRevision: int
    sharingPolicy: Literal["unknown", "evidenced"]
    mentionKind: str
    sharedMentions: list[str] = Field(default_factory=list)
    creditSavings: float | None = None
    coverageComparable: bool
    rankingBlockedReason: str | None = None
    members: list[ProgramComparisonMember] = Field(default_factory=list)
    createdAt: str


class ProgramComparisonSummary(_WireModel):
    comparisonId: UUID
    recordRevision: int
    sharingPolicy: Literal["unknown", "evidenced"]
    mentionKind: str
    sharedMentions: list[str] = Field(default_factory=list)
    creditSavings: float | None = None
    coverageComparable: bool
    rankingBlockedReason: str | None = None
    createdAt: str


class ProgramComparisonCreateRequest(BaseModel):
    programIds: list[UUID]
    addCourseCodes: list[str] = Field(default_factory=list)
    removeCourseCodes: list[str] = Field(default_factory=list)
    workspaceKey: str | None = Field(default=None, max_length=200)


class CatalogEditionItem(_WireModel):
    editionId: UUID
    revision: int
    editionKey: str
    isCurrent: bool
    topicBuildId: UUID | None = None
    sourceIds: list[UUID] = Field(default_factory=list)


class CatalogRefreshBatchResponse(_WireModel):
    batchId: UUID
    collectionId: UUID
    editionKey: str
    status: Literal["staging", "cancelled", "published"]
    baseRevision: int
    stagedRevision: int
    compatible: bool
    currentEdition: CatalogEditionItem | None = None
    stagedEdition: CatalogEditionItem


class CatalogRefreshStatusResponse(_WireModel):
    currentEdition: CatalogEditionItem | None = None
    staging: CatalogRefreshBatchResponse | None = None


class CatalogRefreshStartRequest(BaseModel):
    collectionId: UUID
    editionKey: str
    compatible: bool = True


class CatalogRefreshPublishRequest(BaseModel):
    expectedRevision: int


class ResponseFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    responseId: UUID
    vote: Literal["up", "down"]
    reasonCode: str = Field(min_length=1, max_length=80)
    comment: str | None = Field(default=None, max_length=2000)
    workspaceKey: str | None = Field(default=None, max_length=200)


class ResponseFeedbackResponse(_WireModel):
    feedbackId: UUID
    workspaceKey: str
    responseId: UUID
    revision: int
    vote: Literal["up", "down"]
    reasonCode: str
    comment: str | None = None
    currentVotes: int
    createdAt: str


class EvalCandidateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    redactedPrompt: str = Field(min_length=1, max_length=4000)
    redactedExpected: str = Field(min_length=1, max_length=4000)
    sourceFeedbackId: UUID | None = None


class EvalCandidateResponse(_WireModel):
    candidateId: UUID
    redactedPrompt: str
    redactedExpected: str
    sourceFeedbackId: UUID | None = None
    createdAt: str


class FeedbackTriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedbackId: UUID
    issueType: Literal["missing_evidence", "provider", "explanation", "privacy", "ui"]
    toStatus: Literal["open", "reviewed", "withdrawn"]
    expectedRevision: int = Field(ge=1)
    expectedTriageRevision: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=1000)
    traceId: UUID | None = None
    collectionId: UUID | None = None
    investigationQuery: str | None = Field(default=None, max_length=2000)


class FeedbackTriageResponse(_WireModel):
    feedbackId: UUID
    issueType: str
    triageStatus: str
    triageRevision: int
    voteRevision: int
    gapId: UUID | None = None
    eventId: UUID | None = None


class FeedbackTriageListItem(_WireModel):
    eventId: UUID
    feedbackId: UUID
    issueType: str
    triageStatus: str
    triageRevision: int
    voteRevision: int
    vote: Literal["up", "down"]
    gapId: UUID | None = None
    createdAt: str


class EvalCandidatePromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedbackId: UUID
    redactedPrompt: str = Field(min_length=1, max_length=4000)
    redactedExpected: str = Field(min_length=1, max_length=4000)
    sourceId: UUID
    traceId: UUID
    collectionId: UUID
    reReview: bool = False


class EvalCandidatePromotionResponse(_WireModel):
    candidateId: UUID
    redactedPrompt: str
    redactedExpected: str
    sourceFeedbackId: UUID | None = None
    sourceIndexRevision: int
    promotionState: Literal["current", "stale"]
    reviewState: Literal["reviewed", "re_reviewed"]
    createdAt: str


class DebugExperimentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversationId: UUID
    requestId: UUID
    snapshot: dict


class DebugExperimentResponse(_WireModel):
    experimentId: UUID
    sourceConversationId: UUID
    sourceRequestId: UUID
    state: Literal["recorded"]
    createdAt: str


class PromptVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    templateText: str = Field(min_length=1, max_length=20000)
    versionId: UUID | None = None


class PromptVersionResponse(_WireModel):
    versionId: UUID
    name: str
    templateText: str
    contentDigest: str
    reviewState: Literal["draft", "approved"]
    createdAt: str


class PromptFixtureCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suiteName: str = Field(min_length=1, max_length=120)
    fixtureText: str = Field(min_length=1, max_length=20000)


class PromptFixtureResponse(_WireModel):
    fixtureId: UUID
    suiteName: str
    fixtureDigest: str
    present: bool


class PromptPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suiteName: str = Field(min_length=1, max_length=120)


class PromptPromotionResponse(_WireModel):
    promotionId: UUID
    versionId: UUID
    suiteName: str
    fixtureDigest: str
    status: Literal["published"]
    createdAt: str


# ---- Reviewed course maintenance (phase B) ---------------------------------


class AdminCourse(_WireModel):
    courseId: UUID
    code: str
    subject: str
    title: str
    canonicalCode: str | None = None
    parseStatus: str
    lifecycle: Literal["current", "retired"]
    authority: Literal["atlas", "catalog", "admin_correction", "admin_created"]
    adminRevision: int
    requisitesRaw: str | None = None
    oxford: bool = False
    mentionCount: int = 0
    unresolvedCount: int = 0
    programCount: int = 0
    filename: str | None = None
    contentType: str | None = None
    originKey: str | None = None
    indexRevision: int | None = None
    sourceStatus: str | None = None
    sourceIsCurrent: bool | None = None
    publisher: str | None = None
    sourceUrl: str | None = None
    sourceTitle: str | None = None
    institution: str | None = None
    issuingUnit: str | None = None
    documentRoles: list[str] = []
    applicability: dict[str, Any] = {}
    applicabilityStatus: str | None = None


class AdminCourseListResponse(_WireModel):
    courses: list[AdminCourse]
    total: int
    subjects: list[str]


class AdminCourseMention(_WireModel):
    kind: str
    targetCode: str
    resolved: bool
    position: int | None = None


class AdminCourseNode(_WireModel):
    nodeId: str
    kind: str
    parentNodeId: str | None = None
    position: int
    nodeType: str
    targetCode: str | None = None
    conditionText: str | None = None


class AdminCourseDependent(_WireModel):
    courseId: UUID
    code: str
    title: str
    kind: str
    targetCode: str
    resolved: bool


class AdminCourseProgramReference(_WireModel):
    programId: UUID
    title: str
    kind: str


class AdminCourseChange(_WireModel):
    changeId: UUID
    code: str
    changeKind: Literal["create", "update", "retire", "restore", "baseline_conflict"]
    status: Literal["draft", "published", "rejected", "superseded", "conflicted"]
    expectedRevision: int
    publishedRevision: int | None = None
    baseAuthority: str | None = None
    baseValue: dict[str, Any] = {}
    proposedValue: dict[str, Any] = {}
    reason: str
    actorUserId: UUID | None = None
    publishedByUserId: UUID | None = None
    conflictNote: str | None = None
    createdAt: str
    updatedAt: str
    publishedAt: str | None = None


class AdminCourseDetail(_WireModel):
    course: AdminCourse
    mentions: list[AdminCourseMention]
    nodes: list[AdminCourseNode]
    programs: list[AdminCourseProgramReference]
    dependents: list[AdminCourseDependent]
    changes: list[AdminCourseChange]
    currentCodes: list[str]


class AdminCourseChangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=120)
    changeKind: Literal["create", "update", "retire", "restore"] | None = None
    title: str | None = Field(default=None, max_length=400)
    requisitesRaw: str | None = Field(default=None, max_length=20000)
    sourceId: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)
    expectedRevision: int = Field(default=0, ge=0)


class AdminCourseChangeReject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


class AdminCourseParsedMention(_WireModel):
    kind: str
    targetCode: str
    resolved: bool


class AdminCourseParsedRequisites(_WireModel):
    status: str
    parseStatus: str
    mentions: list[AdminCourseParsedMention]
    unresolved: list[str]
    leftover: list[str]


class AdminCoursePreviewResponse(_WireModel):
    change: AdminCourseChange
    current: dict[str, Any] | None = None
    changedFields: list[str]
    parsed: AdminCourseParsedRequisites | None = None
    programs: list[AdminCourseProgramReference]
    dependents: list[AdminCourseDependent]
    staleAuditCount: int
    activeTopicBuilds: int
    warnings: list[str]


class AdminCoursePublishResponse(_WireModel):
    change: AdminCourseChange


class AdminCourseConflict(_WireModel):
    changeId: UUID
    code: str
    changeKind: str
    status: str
    conflictNote: str | None = None
    createdAt: str
