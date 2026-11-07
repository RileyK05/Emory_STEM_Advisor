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


class ArtifactRevisesInfo(BaseModel):
    artifactId: str
    revisionId: str


class ArtifactRequestInfo(BaseModel):
    kind: Literal["document", "workbook", "html"]
    title: str
    brief: str
    purpose: Literal["create", "revise", "summary"] = "create"
    revises: ArtifactRevisesInfo | None = None


class ResearchDecisionInfo(BaseModel):
    runId: str
    outcome: Literal["recommendation", "information", "clarification", "insufficient_evidence"]
    completeness: Literal["complete", "partial"]
    blocks: list[ResearchBlock]
    artifact: ArtifactRequestInfo | None = None
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


_ArtifactKind = Literal["document", "workbook", "html"]
_ArtifactStatus = Literal["ready", "needs_recalculation"]


class NativeArtifactSummary(_WireModel):
    artifactId: str
    revisionId: str
    kind: _ArtifactKind
    title: str
    status: _ArtifactStatus | None = None


class NativeArtifactRecord(NativeArtifactSummary):
    parentRevisionId: str | None
    spec: dict[str, Any]
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


class ArtifactRunInfo(_WireModel):
    runId: str
    kind: Literal["document", "workbook", "html"]
    title: str
    purpose: Literal["create", "revise", "summary"]


class ConversationTurn(_WireModel):
    sequence: int
    requestId: str
    role: Literal["user", "assistant"]
    text: str
    status: _TurnStatus
    evidence: dict[str, Any]
    artifactRun: ArtifactRunInfo | None = None
    artifactNotice: str | None = None
    createdAt: str


class ConversationRecord(ConversationSummary):
    turns: list[ConversationTurn]


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


class ConversationTurnResponse(_WireModel):
    """The envelope every turn outcome shares; answer, refusal, history, and
    terminal bodies carry their own fields beyond it (passed through unchanged)."""

    type: Literal["answer", "refusal", "history", "failed", "abandoned"]
    conversationId: str
    sequence: int
    artifactRun: ArtifactRunInfo | None = None
    artifactNotice: str | None = None


class AuthSession(_WireModel):
    mode: Literal["development", "oidc"]
    authenticated: bool
    workspaceKey: str | None = Field(default=None, max_length=200)
    displayName: str | None = None
    email: str | None = None
    role: Literal["student", "admin"] | None = None


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
    role: Literal["student", "admin"]
    status: Literal["active", "disabled"]
    conversationCount: int
    activeSessions: int
    createdAt: str
    updatedAt: str
    lastSeenAt: str | None = None


class AdminUserUpdate(BaseModel):
    role: Literal["student", "admin"]
    status: Literal["active", "disabled"]


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
