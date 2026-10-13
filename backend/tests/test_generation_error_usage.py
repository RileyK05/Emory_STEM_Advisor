"""Provider validation failures retain observed usage in both accounting stores."""

from uuid import uuid4

import pytest

from app.config import load_settings
from app.db import queries
from app.generation.evidence import record_attempt
from app.generation.operation import GenerationOperation
from app.llm.base import GenerationRequest, Usage


@pytest.mark.parametrize("observed", [Usage(11, 7, source="provider"), None, {"tokens": 18}])
def test_failed_generation_usage_audited_and_reserved_conservatively(db, observed):
    operation_id = uuid4()
    settings = load_settings()
    operation = GenerationOperation(db, settings, operation_id, "development")
    request = operation.prepare(GenerationRequest(
        messages=[{"role": "user", "content": "classify this input"}], max_output_tokens=50,
    ))
    error = ValueError("provider output failed validation")
    error.usage = observed
    record_attempt(db, None, settings, None, error, 10,
                   operation_id=operation_id, request=request)
    db.commit()
    operation.account(None, error)
    status = db.execute(queries.get("get_operation_budget_for_update"), {
        "operation_id": operation_id,
    }).fetchone()
    # Test inspection may use SQL; application statements remain named queries.
    audit = db.execute("SELECT * FROM model_calls WHERE operation_id = %s", (operation_id,)).fetchone()
    assert audit["status"] == "failed"
    if isinstance(observed, Usage):
        assert status["accounted_tokens"] == 18
        assert status["reserved_tokens"] == 0
        assert (audit["prompt_tokens"], audit["completion_tokens"], audit["usage_source"]) == (11, 7, "provider")
    else:
        assert status["accounted_tokens"] == 0
        assert status["reserved_tokens"] > 0
        assert audit["usage_source"] == "unknown"
        assert audit["prompt_tokens"] is None and audit["completion_tokens"] is None
