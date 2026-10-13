"""Owner-scoped, storage-only reads for immutable conversation checkpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api import deps
from app.api.schemas import ConversationSummaryCheckpoint
from app.auth.dependencies import get_workspace_key
from app.conversations.store import ConversationStore

router = APIRouter(prefix="/api/conversations")
Conn = Annotated[object, Depends(deps.get_conn)]
Workspace = Annotated[str, Depends(get_workspace_key)]


@router.get("/{conversation_id}/summaries", response_model=list[ConversationSummaryCheckpoint])
def list_summaries(conversation_id: UUID, conn: Conn, workspace: Workspace):
    """Return persisted checkpoint records; this endpoint performs no model I/O."""
    return ConversationStore(conn, workspace).summaries(conversation_id)
