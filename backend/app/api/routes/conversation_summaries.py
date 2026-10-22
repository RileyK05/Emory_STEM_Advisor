"""Owner-scoped, storage-only reads for immutable conversation checkpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api import deps
from app.api.schemas import (
    ConversationMemoryInspection,
    ConversationResetResponse,
    ConversationSummaryCheckpoint,
)
from app.auth.dependencies import get_principal, get_workspace_key
from app.auth.models import Principal
from app.conversations.store import ConversationStore

Conn = Annotated[object, Depends(deps.get_conn)]
Workspace = Annotated[str, Depends(get_workspace_key)]


def _memory_access(request: Request, conn: Conn,
                   principal: Annotated[Principal, Depends(get_principal)]):
    from app.flags.service import FeatureFlagService

    FeatureFlagService.require_enabled(
        conn, "memory_inspector", workspace_key=principal.workspace_key,
        settings=request.app.state.settings)


router = APIRouter(prefix="/api/conversations", dependencies=[Depends(_memory_access)])


@router.get("/{conversation_id}/summaries", response_model=list[ConversationSummaryCheckpoint])
def list_summaries(conversation_id: UUID, conn: Conn, workspace: Workspace):
    """Return persisted checkpoint records; this endpoint performs no model I/O."""
    return ConversationStore(conn, workspace).summaries(conversation_id)


@router.get("/{conversation_id}/memory", response_model=ConversationMemoryInspection)
def inspect_memory(conversation_id: UUID, conn: Conn, workspace: Workspace):
    """Inspect conversation checkpoints, coverage ranges, exact references, and research state."""
    store = ConversationStore(conn, workspace)
    conv = store.get(conversation_id)
    summaries = store.summaries(conversation_id)
    research_nb = store.latest_research_notebook(conversation_id)
    return {
        "conversationId": str(conv["conversation_id"]),
        "contextEpoch": conv.get("context_epoch", 0),
        "contextBoundarySequence": conv.get("context_boundary_sequence", 0),
        "contextResetAt": conv["context_reset_at"].isoformat() if conv.get("context_reset_at") else None,
        "checkpoints": summaries,
        "researchNotebook": research_nb,
    }


@router.post("/{conversation_id}/memory/reset", response_model=ConversationResetResponse)
def reset_memory(conversation_id: UUID, conn: Conn, workspace: Workspace):
    """Reset context epoch boundary, excluding prior turns from future context and compaction."""
    store = ConversationStore(conn, workspace)
    conv = store.reset_memory(conversation_id)
    return {
        "conversationId": conv["conversationId"],
        "contextEpoch": conv["contextEpoch"],
        "contextBoundarySequence": conv["contextBoundarySequence"],
        "contextResetAt": conv["contextResetAt"],
        "message": "Conversation context memory has been reset. Prior turns remain in transcript history but are excluded from future model context.",
    }
