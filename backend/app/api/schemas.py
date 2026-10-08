"""HTTP request/response schemas. Field names mirror frontend/src/api/types.ts."""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    collectionId: str | None = None


class SeamStatus(BaseModel):
    name: str
    enabled: bool
    state: str
    reason: str | None = None
    candidateCount: int


class Contribution(BaseModel):
    chunkId: str
    seams: list[str]
    normalizedScore: float
    sourceSlot: int


class ContextItem(BaseModel):
    chunkId: str
    sourceName: str
    locatorLabel: str
    text: str


class PromptInfo(BaseModel):
    system: str
    user: str


class ModelCallInfo(BaseModel):
    provider: str
    modelId: str
    promptTokens: int
    completionTokens: int
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
    sourceName: str
    locatorLabel: str
    chunkId: str


class AnswerResponse(BaseModel):
    type: str = "answer"
    answer: str
    citations: list[Citation]
    trace: RetrievalTrace


class RefusalResponse(BaseModel):
    type: str = "refusal"
    reason: str
    trace: RetrievalTrace


class SourceDoc(BaseModel):
    id: str
    name: str
    locatorType: str
    status: str
    chunkCount: int


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


class SubgraphView(BaseModel):
    nodes: list[CourseDoc]
    edges: list[dict]
