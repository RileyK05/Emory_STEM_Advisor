"""Tests for feature flags, cohort rollouts, deterministic precedence, and kill-switches (Item 17)."""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.config import load_settings
from app.db import queries
from app.flags.service import FeatureFlagService
from tests.helpers import api_client


def _make_user(db, workspace_key: str, role: str = "student"):
    user_id = uuid4()
    db.execute(
        queries.get("upsert_app_user"),
        {
            "user_id": user_id,
            "issuer": "https://auth.emory.edu",
            "subject": str(user_id),
            "workspace_key": workspace_key,
            "display_name": f"{role.title()} {user_id}",
            "email": f"{user_id}@emory.edu",
            "bootstrap": False,
        },
    )
    db.execute(
        queries.get("admin_update_user"),
        {"user_id": user_id, "role": role, "status": "active"},
    )
    db.commit()
    return user_id


def test_flag_precedence_and_kill_switch(db):
    user_id = _make_user(db, "ws-flag-test")
    cohort = "pilot_cohort"
    FeatureFlagService.assign_cohort(db, user_id, cohort)

    # 1. Flag with no overrides returns default
    # 'memory_inspector' default is True
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-flag-test", user_id=user_id) is True

    # 2. Global override can disable it
    FeatureFlagService.set_override(db, "memory_inspector", "global", "*", False, reason="Maintenance")
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-flag-test", user_id=user_id) is False

    # 3. Cohort override can enable it for pilot cohort
    FeatureFlagService.set_override(db, "memory_inspector", "cohort", cohort, True, reason="Pilot access")
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-flag-test", user_id=user_id) is True

    # Another user not in the cohort gets False (from global override)
    other_user = _make_user(db, "ws-other")
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-other", user_id=other_user) is False

    # 4. Workspace override takes precedence over cohort
    FeatureFlagService.set_override(db, "memory_inspector", "workspace", "ws-flag-test", False, reason="Workspace opt-out")
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-flag-test", user_id=user_id) is False

    # 5. Immediate kill switch overrides all
    # Re-enable workspace
    FeatureFlagService.set_override(db, "memory_inspector", "workspace", "ws-flag-test", True)
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-flag-test", user_id=user_id) is True

    # Trip kill switch
    FeatureFlagService.set_kill_switch(db, "memory_inspector", True, reason="Emergency zero-day")
    assert FeatureFlagService.evaluate(db, "memory_inspector", workspace_key="ws-flag-test", user_id=user_id) is False


def test_env_ceiling_cannot_be_bypassed_by_cohort_or_workspace(db):
    user_id = _make_user(db, "ws-env-test")
    cohort = "advanced_planning"
    FeatureFlagService.assign_cohort(db, user_id, cohort)

    # 'query_planning' has env ceiling 'QUERY_PLANNING_ENABLED'
    # Even with workspace and cohort overrides explicitly set to True:
    FeatureFlagService.set_override(db, "query_planning", "cohort", cohort, True)
    FeatureFlagService.set_override(db, "query_planning", "workspace", "ws-env-test", True)

    # When Settings has query_planning_enabled = False (env ceiling disabled):
    class FakeSettings:
        query_planning_enabled = False

    res = FeatureFlagService.evaluate(
        db,
        "query_planning",
        workspace_key="ws-env-test",
        user_id=user_id,
        settings=FakeSettings(),
    )
    assert res is False, "Cohort or workspace policy must never enable a feature disabled globally by env ceiling"

    # When env ceiling is True, workspace/cohort override is honored:
    FakeSettings.query_planning_enabled = True
    res_allowed = FeatureFlagService.evaluate(
        db,
        "query_planning",
        workspace_key="ws-env-test",
        user_id=user_id,
        settings=FakeSettings(),
    )
    assert res_allowed is True


def test_admin_flag_management_and_audit(db, collection):
    client = api_client(db, collection)

    # 1. List flags
    res = client.get("/api/admin/flags")
    assert res.status_code == 200
    flags = res.json()
    assert len(flags) >= 7
    names = {f["flagName"] for f in flags}
    assert "tools" in names
    assert "memory_inspector" in names

    # 2. Set override
    override_res = client.post(
        "/api/admin/flags/tools/overrides",
        json={"scopeType": "cohort", "scopeId": "beta_testers", "enabled": True, "reason": "Beta testing tools"},
    )
    assert override_res.status_code == 200
    ov = override_res.json()
    assert ov["flagName"] == "tools"
    assert ov["scopeType"] == "cohort"
    assert ov["scopeId"] == "beta_testers"
    assert ov["enabled"] is True
    updated = client.post("/api/admin/flags/tools/overrides", json={
        "scopeType": "cohort", "scopeId": "beta_testers", "enabled": False,
        "expectedUpdatedAt": ov["updatedAt"],
    })
    assert updated.status_code == 200, updated.text
    conflict = client.post("/api/admin/flags/tools/overrides", json={
        "scopeType": "cohort", "scopeId": "beta_testers", "enabled": True,
        "expectedUpdatedAt": ov["updatedAt"],
    })
    assert conflict.status_code == 409
    assert client.get("/api/admin/flags/tools/overrides").json()[0]["enabled"] is False

    # 3. Toggle kill switch
    kill_res = client.post(
        "/api/admin/flags/tools/kill",
        json={"killSwitchActive": True, "reason": "Security stop"},
    )
    assert kill_res.status_code == 200
    assert kill_res.json()["killSwitchActive"] is True

    user_id = _make_user(db, "cohort-member")
    member = client.post("/api/admin/cohorts/pilot/users", json={"userId": str(user_id)})
    assert member.status_code == 200, member.text
    members = client.get("/api/admin/cohorts/pilot/users").json()
    assert [row["userId"] for row in members] == [str(user_id)]
    assert client.delete(f"/api/admin/cohorts/pilot/users/{user_id}").status_code == 204
    assert client.get("/api/admin/cohorts/pilot/users").json() == []

    # 4. Verify audit entries were created
    audits = db.execute(
        queries.get("admin_list_audit"),
        {"limit": 10},
    ).fetchall()
    actions = {a["action"] for a in audits}
    assert "admin.flag_override_set" in actions
    assert "admin.flag_kill_switch_toggled" in actions


def test_effective_flags_endpoint(db, collection):
    client = api_client(db, collection)

    res = client.get("/api/flags/effective")
    assert res.status_code == 200
    data = res.json()
    assert "flags" in data
    assert isinstance(data["flags"], dict)
    assert "memory_inspector" in data["flags"]
    assert data["flags"]["memory_inspector"] is True
    assert "conversation_compaction" in data["flags"]
    # hf-local environment defaults compaction ceiling to False
    assert data["flags"]["conversation_compaction"] is False

    conversation = client.post("/api/conversations", json={"title": "Memory policy"}).json()
    conversation_id = conversation["conversationId"]
    assert client.get(f"/api/conversations/{conversation_id}/memory").status_code == 200
    FeatureFlagService.set_kill_switch(db, "memory_inspector", True, "test stop")
    for path, method in (("memory", client.get), ("summaries", client.get),
                         ("memory/reset", client.post)):
        assert method(f"/api/conversations/{conversation_id}/{path}").status_code == 403
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 200
    FeatureFlagService.set_kill_switch(db, "advising_summary_export", True, "test stop")
    assert client.post(f"/api/conversations/{conversation_id}/export-summary", json={}).status_code == 403


@pytest.mark.parametrize("flag,attribute", [
    ("query_planning", "query_planning_enabled"),
    ("tools", "tools_enabled"),
    ("advising_research", "advising_research_enabled"),
    ("citation_repair", "answer_citation_repair_enabled"),
    ("conversation_compaction", "conversation_compaction_enabled"),
])
def test_execution_settings_enforce_cohorts_and_current_kills(db, flag, attribute):
    user_id = _make_user(db, "policy-workspace")
    FeatureFlagService.assign_cohort(db, user_id, "pilot")
    settings = replace(load_settings(), llm_provider="openai", tools_enabled=True,
                       advising_research_enabled=True, query_planning_enabled=True,
                       answer_citation_repair_enabled=True, conversation_compaction_enabled=True)
    assert getattr(FeatureFlagService.execution_settings(db, settings, "policy-workspace"), attribute)
    FeatureFlagService.set_override(db, flag, "global", "*", False)
    assert not getattr(FeatureFlagService.execution_settings(db, settings, "policy-workspace"), attribute)
    FeatureFlagService.set_override(db, flag, "cohort", "pilot", True)
    assert getattr(FeatureFlagService.execution_settings(db, settings, "policy-workspace"), attribute)
    FeatureFlagService.set_kill_switch(db, flag, True, "Emergency stop")
    assert not getattr(FeatureFlagService.execution_settings(db, settings, "policy-workspace"), attribute)
    assert not getattr(FeatureFlagService.execution_settings(db, settings, "other-workspace"), attribute)
