
import pytest

from app.jobs.store import MAX_PAYLOAD_BYTES, retry_delay


def test_retry_delay_is_capped_exponential():
    assert retry_delay(1) == 1
    assert retry_delay(2) == 2
    assert retry_delay(9) == 256
    assert retry_delay(10) == 300
    assert retry_delay(20, base_seconds=2, cap_seconds=5) == 5


@pytest.mark.parametrize("attempt", [0, -1])
def test_retry_delay_rejects_invalid_attempt(attempt):
    with pytest.raises(ValueError):
        retry_delay(attempt)


def test_store_validates_attempts_and_payload_size():
    from app.jobs import JobStore

    store = JobStore(lambda: None)
    with pytest.raises(ValueError, match="max_attempts"):
        store.create(kind="x", scope_key="x", idempotency_key="x", payload={}, max_attempts=21)
    with pytest.raises(ValueError, match="payload exceeds"):
        store.create(
            kind="x", scope_key="x", idempotency_key="y",
            payload={"blob": "x" * MAX_PAYLOAD_BYTES},
        )
