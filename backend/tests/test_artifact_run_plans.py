from uuid import uuid4

import pytest
from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from app.db import queries
from app.db.connection import connect
from app.jobs import JobStore, LeaseLost
from app.jobs.artifact_plans import (
    ArtifactBindingStale,
    ArtifactPlanConflict,
    ArtifactPlanError,
    ArtifactPlanNotFound,
    ArtifactRunExcerpt,
    ArtifactRunPlanInput,
    ArtifactRunPlanStore,
    ArtifactRunQuotas,
    ArtifactRunSource,
    ArtifactRunUnitInput,
    ArtifactUnitConflict,
    ArtifactUnitNotReady,
)
from app.operations.budget import OperationBudgetController, OperationLimits


def make_job(dsn, *, workspace="workspace-a"):
    store = JobStore(lambda: connect(dsn))
    job = store.create(
        kind="artifact.run", scope_key=workspace, idempotency_key=f"run-{uuid4()}",
        payload={"kind": "document", "inputs": {"prompt": "Build a guide"}},
    )
    claimed = store.claim("plan-worker", lease_seconds=60)
    assert claimed.job_id == job.job_id
    return store, claimed


def unit(key, *, deps=(), sources=(), kind="section", profile_id=None, profile_revision=None,
         snapshot=None):
    return ArtifactRunUnitInput(
        unit_key=key, unit_kind=kind, input_snapshot=snapshot or {"title": key},
        source_ids=tuple(sources), depends_on=tuple(deps), profile_id=profile_id,
        profile_revision=profile_revision,
    )


def spec(claimed, collection_id, *, units=None, sources=(), profile_id=None,
         profile_revision=None, frozen=None, quotas=None):
    selected = tuple(units or (unit("intro"),))
    return ArtifactRunPlanInput(
        workspace_key=claimed.scope_key, collection_id=collection_id, artifact_kind="document",
        frozen_inputs=frozen or {"prompt": "Create a biology study guide", "term": "2026 fall"},
        units=selected, sources=tuple(sources), quotas=quotas or ArtifactRunQuotas(),
        profile_id=profile_id, profile_revision=profile_revision,
    )


def add_indexed_source(db, collection_id, *, workspace_key=None, index_revision=3):
    source_id = uuid4()
    file_hash = f"sha256-{uuid4().hex}"
    db.execute(
        "INSERT INTO sources (source_id,collection_id,workspace_key,filename,content_type,"
        "file_hash,raw_path,status,index_revision) VALUES (%s,%s,%s,%s,%s,%s,%s,'indexed',%s)",
        (source_id, collection_id, workspace_key, "handbook.md", "text/markdown", file_hash,
         "test://source", index_revision),
    )
    db.commit()
    return ArtifactRunSource(source_id, collection_id, file_hash, index_revision)


def add_profile(db):
    profile_id = uuid4()
    db.execute(queries.get("insert_connection_profile"), {
        "profile_id": profile_id, "name": f"p-{profile_id}", "provider": "openai",
        "model_id": "test-model", "base_url": None, "account_id": None,
        "secret_ciphertext": None, "provider_options": Jsonb({}), "roles": ["complex"],
        "capabilities": ["structured_output"], "data_scopes": ["institutional_corpus"],
        "enabled": True, "priority": 1,
    })
    db.commit()
    return profile_id


def reserve_call(db, job_id, workspace_key):
    controller = OperationBudgetController()
    controller.ensure(db, job_id, workspace_key, OperationLimits())
    return controller.reserve_before_call(
        db, job_id, prompt_reserved_tokens=10, requested_output_tokens=20
    )


def test_plan_freeze_is_owner_scoped_and_idempotent(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_input = spec(claimed, collection, units=(unit("z_intro"), unit("a_summary")))

    first = store.create_plan(claimed.job_id, claimed.lease_token, plan_input)
    repeated = store.create_plan(claimed.job_id, claimed.lease_token, plan_input)
    assert repeated.input_fingerprint == first.input_fingerprint
    assert repeated.status == "ready"
    assert [row.unit_key for row in store.list_units(claimed.job_id, claimed.scope_key)] == [
        "z_intro", "a_summary"
    ]
    reordered = spec(
        claimed, collection, units=(unit("a_summary"), unit("z_intro"))
    )
    with pytest.raises(ArtifactPlanConflict):
        store.create_plan(claimed.job_id, claimed.lease_token, reordered)
    with pytest.raises(ArtifactPlanNotFound):
        store.get_plan(claimed.job_id, "workspace-b")
    with pytest.raises(ArtifactPlanNotFound):
        store.list_units(claimed.job_id, "workspace-b")
    with pytest.raises(ArtifactPlanConflict):
        store.create_plan(
            claimed.job_id, claimed.lease_token,
            spec(claimed, collection, frozen={"prompt": "a changed prompt"}),
        )


def test_plan_create_requires_job_owner_scope(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    foreign = ArtifactRunPlanInput(
        workspace_key="workspace-b", collection_id=collection, artifact_kind="document",
        frozen_inputs={"prompt": "private"}, units=(unit("intro"),),
    )
    with pytest.raises(LeaseLost):
        plan_store.create_plan(claimed.job_id, claimed.lease_token, foreign)
    with pytest.raises(ArtifactPlanNotFound):
        plan_store.get_plan(claimed.job_id, claimed.scope_key)


def test_plan_rejects_cycles_unknown_dependencies_and_nonfinite_json(collection):
    claimed_workspace = "workspace-a"
    base = {"workspace_key": claimed_workspace, "collection_id": collection,
            "artifact_kind": "document"}
    with pytest.raises(ValueError, match="cycle"):
        ArtifactRunPlanInput(
            **base, frozen_inputs={}, units=(unit("a", deps=("b",)), unit("b", deps=("a",)))
        )
    with pytest.raises(ValueError, match="dependency"):
        ArtifactRunPlanInput(
            **base, frozen_inputs={}, units=(unit("a", deps=("missing",)),)
        )
    with pytest.raises(ValueError, match="finite JSON"):
        ArtifactRunPlanInput(
            **base, frozen_inputs={"number": float("nan")}, units=(unit("a"),)
        )
    with pytest.raises(ValueError, match="max_units"):
        ArtifactRunQuotas(max_units=101)
    with pytest.raises(ValueError, match="max_units quota"):
        ArtifactRunPlanInput(
            **base, frozen_inputs={}, units=(unit("a"), unit("b")),
            quotas=ArtifactRunQuotas(max_units=1),
        )


def test_checkpoint_replay_and_dependencies_are_immutable(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(
        claimed.job_id, claimed.lease_token,
        spec(claimed, collection, units=(unit("section-1"), unit("section-2", deps=("section-1",)))),
    )
    first_reservation = reserve_call(db, claimed.job_id, claimed.scope_key)
    with pytest.raises(ArtifactUnitNotReady):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "section-2",
            first_reservation.attempt_no,
        )
    calling = plan_store.mark_calling(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "section-1",
        first_reservation.attempt_no,
    )
    assert calling.status == "calling"
    checkpoint = {"blocks": [{"id": "section-1", "text": "Validated section"}]}
    validated = plan_store.mark_validated(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "section-1", checkpoint
    )
    assert validated.status == "validated"
    assert validated.checkpoint == checkpoint
    replayed = plan_store.list_units(claimed.job_id, claimed.scope_key)[0]
    assert replayed.checkpoint == checkpoint
    assert replayed.checkpoint_fingerprint == validated.checkpoint_fingerprint
    with pytest.raises(ArtifactUnitConflict):
        plan_store.mark_validated(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "section-1",
            {"blocks": []},
        )
    local = plan_store.mark_validated(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "section-2",
        {"blocks": [{"id": "section-2", "text": "Deterministic assembly input"}]},
    )
    assert local.status == "validated"
    assert plan_store.get_plan(claimed.job_id, claimed.scope_key).status == "validated"
    with pytest.raises(RaiseException, match="checkpoint is immutable"):
        db.execute(
            "UPDATE artifact_run_units SET checkpoint=%s WHERE job_id=%s AND unit_id=%s",
            (Jsonb({"blocks": []}), claimed.job_id, validated.unit_id),
        )


def test_forward_dependency_preserves_output_order_but_waits_for_parent(
    migrated_dsn, db, collection
):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(
        claimed.job_id, claimed.lease_token,
        spec(claimed, collection, units=(unit("overview", deps=("detail",)), unit("detail"))),
    )
    units = plan_store.list_units(claimed.job_id, claimed.scope_key)
    assert [row.unit_key for row in units] == ["overview", "detail"]
    with pytest.raises(ArtifactUnitNotReady):
        plan_store.mark_validated(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "overview", {"text": "Overview"}
        )
    plan_store.mark_validated(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "detail", {"text": "Detail"}
    )
    assert plan_store.mark_validated(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "overview", {"text": "Overview"}
    ).status == "validated"


def test_calling_unit_binds_one_reserved_operation_attempt(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, spec(claimed, collection))
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)

    calling = plan_store.mark_calling(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "intro", reservation.attempt_no
    )
    assert calling.status == "calling"
    with pytest.raises(ArtifactUnitConflict):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "intro", reservation.attempt_no
        )
    assert db.execute(
        "SELECT count(*) AS n FROM artifact_run_unit_calls WHERE job_id=%s", (claimed.job_id,)
    ).fetchone()["n"] == 1


def test_unit_call_rejects_expired_operation_reservation(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, spec(claimed, collection))
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)
    db.execute(
        "UPDATE operations SET started_at=clock_timestamp()-interval '2 seconds', "
        "deadline_at=clock_timestamp()-interval '1 second' "
        "WHERE operation_id=%s", (claimed.job_id,),
    )
    db.commit()

    with pytest.raises(ArtifactUnitConflict, match="reservation"):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "intro",
            reservation.attempt_no,
        )
    assert plan_store.list_units(claimed.job_id, claimed.scope_key)[0].status == "pending"


def test_operation_attempt_cannot_be_reused_by_another_unit(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(
        claimed.job_id, claimed.lease_token,
        spec(claimed, collection, units=(unit("first"), unit("second"))),
    )
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)
    plan_store.mark_calling(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "first", reservation.attempt_no
    )
    with pytest.raises(ArtifactUnitConflict):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "second", reservation.attempt_no
        )


def test_unknown_call_is_terminal_and_cannot_be_retried(migrated_dsn, db, collection):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, spec(claimed, collection))
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)
    plan_store.mark_calling(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "intro", reservation.attempt_no
    )
    unknown = plan_store.mark_unknown(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "intro"
    )
    assert unknown.status == "unknown"
    assert unknown.failure_code == "unknown_outcome"
    with pytest.raises(ArtifactUnitConflict):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "intro", reservation.attempt_no
        )
    assert plan_store.get_plan(claimed.job_id, claimed.scope_key).status == "unknown"


@pytest.mark.parametrize("fence", ["expired", "cancelled"])
def test_unit_transition_requires_live_uncancelled_lease(
    migrated_dsn, db, collection, fence
):
    jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, spec(claimed, collection))
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)
    if fence == "expired":
        db.execute(
            "UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
            (claimed.job_id,),
        )
        db.commit()
    else:
        jobs.cancel(claimed.job_id)
    with pytest.raises(LeaseLost):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "intro", reservation.attempt_no
        )
    assert plan_store.list_units(claimed.job_id, claimed.scope_key)[0].status == "pending"


@pytest.mark.parametrize("stale", ["source", "profile"])
def test_unit_call_fails_when_frozen_source_or_profile_changes(
    migrated_dsn, db, collection, stale
):
    _jobs, claimed = make_job(migrated_dsn)
    source = add_indexed_source(db, collection)
    profile_id = add_profile(db) if stale == "profile" else None
    profile_revision = 1 if profile_id else None
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(
        claimed.job_id, claimed.lease_token,
        spec(
            claimed, collection,
            units=(unit("intro", sources=(source.source_id,), profile_id=profile_id,
                        profile_revision=profile_revision),),
            sources=(source,), profile_id=profile_id, profile_revision=profile_revision,
        ),
    )
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)
    if stale == "source":
        db.execute("UPDATE sources SET index_revision=index_revision+1 WHERE source_id=%s",
                   (source.source_id,))
    else:
        db.execute("UPDATE connection_profiles SET revision=revision+1 WHERE profile_id=%s",
                   (profile_id,))
    db.commit()

    with pytest.raises(ArtifactBindingStale):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "intro", reservation.attempt_no
        )
    assert plan_store.list_units(claimed.job_id, claimed.scope_key)[0].status == "pending"


def test_partial_plan_is_not_validation_ready_and_checkpoint_quota_is_enforced(
    migrated_dsn, db, collection
):
    _jobs, claimed = make_job(migrated_dsn)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    quotas = ArtifactRunQuotas(max_checkpoint_bytes=40)
    plan_store.create_plan(
        claimed.job_id, claimed.lease_token,
        spec(claimed, collection, units=(unit("part-a"), unit("part-b")), quotas=quotas),
    )
    with pytest.raises(ArtifactPlanNotFound):
        plan_store.get_plan(uuid4(), claimed.scope_key)
    with pytest.raises(ArtifactPlanError, match="exceed the plan quota"):
        plan_store.mark_validated(
            claimed.job_id, claimed.scope_key, claimed.lease_token, "part-a",
            {"blocks": [{"text": "This checkpoint is larger than forty bytes."}]},
        )
    assert plan_store.get_plan(claimed.job_id, claimed.scope_key).status == "ready"
    assert all(unit.status == "pending" for unit in plan_store.list_units(
        claimed.job_id, claimed.scope_key
    ))


def test_frozen_excerpt_survives_chunk_replacement_and_fails_closed(
    migrated_dsn, db, collection
):
    _jobs, claimed = make_job(migrated_dsn)
    source = add_indexed_source(db, collection)
    locator_id, chunk_id = uuid4(), uuid4()
    db.execute(
        "INSERT INTO locators(locator_id,source_id,ordinal,locator_type,label,start_char,end_char) "
        "VALUES(%s,%s,0,'page','page 1',0,17)", (locator_id, source.source_id),
    )
    db.execute(
        "INSERT INTO chunks(chunk_id,source_id,locator_id,chunk_index,start_char,end_char,token_count,text) "
        "VALUES(%s,%s,%s,0,0,17,2,'Exact source text')",
        (chunk_id, source.source_id, locator_id),
    )
    db.commit()
    excerpt = ArtifactRunExcerpt(
        excerpt_id=1, source_id=source.source_id, chunk_id=chunk_id, locator_id=locator_id,
        source_name="handbook.md", locator_label="page 1", text_snapshot="Exact source text",
    )
    plan_input = ArtifactRunPlanInput(
        workspace_key=claimed.scope_key, collection_id=collection,
        artifact_kind="document", frozen_inputs={"excerpts": [{"source_id": str(source.source_id)}]},
        units=(unit("intro", sources=(source.source_id,)),), sources=(source,),
        excerpts=(excerpt,),
    )
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, plan_input)
    reservation = reserve_call(db, claimed.job_id, claimed.scope_key)

    # Reindexing deletes and recreates these rows. The durable snapshot remains,
    # while the next execution fence rejects it as stale.
    db.execute("DELETE FROM chunks WHERE chunk_id=%s", (chunk_id,))
    db.execute("DELETE FROM locators WHERE locator_id=%s", (locator_id,))
    db.execute("UPDATE sources SET index_revision=index_revision+1 WHERE source_id=%s",
               (source.source_id,))
    db.commit()
    with pytest.raises(ArtifactBindingStale):
        plan_store.mark_calling(
            claimed.job_id, claimed.scope_key, claimed.lease_token,
            "intro", reservation.attempt_no,
        )


def test_plan_fingerprint_is_stable_across_releases():
    """Stored plans are matched on replay by this digest; it must never change silently."""
    from uuid import UUID

    from app.jobs.artifact_plans import (
        ArtifactRunExcerpt,
        ArtifactRunPlanInput,
        ArtifactRunQuotas,
        ArtifactRunSource,
        ArtifactRunUnitInput,
        _plan_fingerprint,
        _validate_dag,
    )

    def u(n):
        return UUID(int=n)

    collection = u(1)
    sources = (ArtifactRunSource(u(20), collection, "h2", 3), ArtifactRunSource(u(10), collection, "h1", 1))
    excerpts = (ArtifactRunExcerpt(2, u(10), u(31), u(41), "B", "p.2", "text two"),
                ArtifactRunExcerpt(1, u(20), u(30), u(40), "A", "p.1", "text one"))
    units = (ArtifactRunUnitInput("s1", "section", {"k": 1}, (u(20), u(10)), (), None, None),
             ArtifactRunUnitInput("assemble", "assembly", {"z": 2}, (u(10),), ("s1",), None, None))
    spec = ArtifactRunPlanInput("ws", collection, "document", {"a": 1}, units, sources, excerpts,
                                ArtifactRunQuotas(max_units=5))
    _validate_dag(spec)
    assert _plan_fingerprint(spec) == "a05c7152397ebc7d481bd0afda1237b126eb1d4e08ef8f4c51e7e895c2447b3a"
