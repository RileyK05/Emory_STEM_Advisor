from uuid import uuid4

import pytest

from app.db import queries
from app.db.connection import connect
from app.generation.controller import ToolConversationController
from app.llm.base import GenerationRequest, GenerationResult, TextMessage, ToolCall
from app.tools.executions import (
    ToolCallChanged,
    ToolExecutionIndeterminate,
    ToolExecutionInProgress,
    ToolExecutionLedger,
    ToolLedgerError,
)
from app.tools.registry import (
    ToolCapability,
    ToolInputModel,
    ToolOutputModel,
    ToolRegistry,
    ToolScope,
    ToolSpec,
)


def _operation(db, workspace="ws"):
    operation_id = uuid4()
    db.execute(queries.get("create_operation_budget"), {
        "operation_id": operation_id,
        "workspace_key": workspace,
        "active_budget_seconds": 120,
        "max_calls": 2,
        "total_token_budget": 16000,
        "per_call_output_cap": 1024,
        "per_call_timeout_seconds": 60,
    })
    db.commit()
    return operation_id


def test_tool_execution_replays_completed_result_and_binds_request(migrated_dsn, db):
    operation_id = _operation(db)
    ledger = ToolExecutionLedger(lambda: connect(migrated_dsn))
    args = {"expression": "2+2", "precision": 2}
    first = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="call-a", tool_id="advisor_calculate", arguments=args)
    assert first.claim is not None
    ledger.complete(first.claim, '{"result":"4"}')

    replay = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="call-a", tool_id="advisor_calculate",
        arguments={"precision": 2, "expression": "2+2"})
    assert replay.replayed and replay.replay_json == '{"result":"4"}'
    with pytest.raises(ToolCallChanged):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
            call_id="call-a", tool_id="advisor_calculate",
            arguments={"expression": "3+3", "precision": 2})
    with pytest.raises(ToolCallChanged):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="other",
            call_id="call-a", tool_id="advisor_calculate", arguments=args)
    with pytest.raises(ToolCallChanged):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
            call_id="call-a", tool_id="advisor_calculate", arguments=args,
            scope_fingerprint="a" * 64)


def test_in_progress_and_unknown_execution_fail_closed(migrated_dsn, db):
    operation_id = _operation(db)
    ledger = ToolExecutionLedger(lambda: connect(migrated_dsn))
    first = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="call-a", tool_id="advisor_calculate", arguments={"expression": "2"})
    with pytest.raises(ToolExecutionInProgress):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
            call_id="call-a", tool_id="advisor_calculate", arguments={"expression": "2"})
    ledger.mark_unknown(first.claim)
    with pytest.raises(ToolExecutionIndeterminate):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
            call_id="call-a", tool_id="advisor_calculate", arguments={"expression": "2"})


def test_tool_execution_result_size_is_bounded(migrated_dsn, db):
    operation_id = _operation(db)
    ledger = ToolExecutionLedger(lambda: connect(migrated_dsn), max_result_bytes=8)
    claim = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="call-a", tool_id="tool", arguments={}).claim
    with pytest.raises(Exception, match="size limit"):
        ledger.complete(claim, '"too long"')
    with pytest.raises(ToolExecutionIndeterminate):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
            call_id="call-a", tool_id="tool", arguments={})


def test_controller_replays_completed_tool_across_controller_runs(migrated_dsn, db):
    operation_id = _operation(db)
    ledger = ToolExecutionLedger(lambda: connect(migrated_dsn))
    executions = []

    class Input(ToolInputModel):
        value: str

    class Output(ToolOutputModel):
        receipt: str

    registry = ToolRegistry([ToolSpec("test_side_effect", "test", Input, Output,
        ToolCapability.CALCULATE,
        lambda parsed, _scope: executions.append(parsed.value) or Output(receipt="stored"))])

    class Provider:
        def __init__(self):
            self.responses = iter([GenerationResult(tool_calls=(ToolCall(
                "same-provider-id", "test_side_effect", {"value": "draft"}),)),
                GenerationResult(text="finished")])

        def generate(self, _request):
            return next(self.responses)

    class Operation:
        def prepare(self, request):
            return request

        def account(self, _result, _error):
            pass

    for _ in range(2):
        ToolConversationController().run(Provider(),
            GenerationRequest((TextMessage("user", "stage"),)), registry=registry,
            scope=ToolScope("ws", frozenset({ToolCapability.CALCULATE})),
            operation=Operation(), ledger=ledger, operation_id=operation_id,
            workspace_key="ws")
    assert executions == ["draft"]


def test_rejected_tool_request_is_a_replayable_result_the_model_can_correct(migrated_dsn, db):
    operation_id = _operation(db)
    ledger = ToolExecutionLedger(lambda: connect(migrated_dsn))

    class Input(ToolInputModel):
        value: str

    class Output(ToolOutputModel):
        receipt: str

    registry = ToolRegistry([ToolSpec("test_read", "test", Input, Output,
        ToolCapability.CALCULATE, lambda parsed, _scope: Output(receipt=parsed.value))])
    requests = []

    class Provider:
        def __init__(self):
            self.responses = iter([
                GenerationResult(tool_calls=(ToolCall("bad", "test_read", {"value": 3}),)),
                GenerationResult(tool_calls=(ToolCall("good", "test_read", {"value": "ok"}),)),
                GenerationResult(text="finished")])

        def generate(self, request):
            requests.append(request)
            return next(self.responses)

    class Operation:
        def prepare(self, request):
            return request

        def account(self, _result, _error):
            pass

    result = ToolConversationController().run(Provider(),
        GenerationRequest((TextMessage("user", "read"),)), registry=registry,
        scope=ToolScope("ws", frozenset({ToolCapability.CALCULATE})),
        operation=Operation(), ledger=ledger, operation_id=operation_id, workspace_key="ws",
        scope_fingerprint="f" * 64)
    assert result.text == "finished"
    rejection = next(m for m in requests[1].messages if getattr(m, "tool_call_id", None) == "bad")
    assert "schema validation" in rejection.content
    replay = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="bad", tool_id="test_read", arguments={"value": 3}, scope_fingerprint="f" * 64)
    assert replay.replay_json == rejection.content


def test_durable_tool_call_limit_is_shared_across_retries(migrated_dsn, db):
    operation_id = _operation(db)
    ledger = ToolExecutionLedger(lambda: connect(migrated_dsn), max_tool_calls=1)
    first = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="first", tool_id="read", arguments={})
    ledger.complete(first.claim, '{"ok":true}')
    with pytest.raises(ToolLedgerError, match="limit"):
        ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
            call_id="second", tool_id="read", arguments={})
    # The completed call remains replayable even when the operation's budget is full.
    replay = ledger.claim_or_replay(operation_id=operation_id, workspace_key="ws",
        call_id="first", tool_id="read", arguments={})
    assert replay.replay_json == '{"ok":true}'
