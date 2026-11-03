"""Tests for student privacy boundaries, durable roles, and access grants (Item 15)."""


import pytest
from fastapi import HTTPException

from app.admin.store import AdminStore
from app.auth.capabilities import (
    Capability,
    has_capability,
    require_capability,
)
from app.auth.models import Principal
from tests.helpers import create_user


def test_role_capability_matrix():
    # Student
    assert has_capability("student", Capability.READ_OWN_WORKSPACE)
    assert has_capability("student", Capability.WRITE_OWN_WORKSPACE)
    assert not has_capability("student", Capability.READ_AUDIT)
    assert not has_capability("student", Capability.MANAGE_USERS)

    # Advisor
    assert has_capability("advisor", Capability.READ_OWN_WORKSPACE)
    assert has_capability("advisor", Capability.WRITE_OWN_WORKSPACE)
    assert not has_capability("advisor", Capability.MANAGE_USERS)

    # Auditor
    assert has_capability("auditor", Capability.READ_AUDIT)
    assert has_capability("auditor", Capability.EXPORT_AUDIT)
    assert has_capability("auditor", Capability.VIEW_TELEMETRY)
    assert not has_capability("auditor", Capability.WRITE_OWN_WORKSPACE)
    assert not has_capability("auditor", Capability.MANAGE_USERS)

    # Admin
    for cap in Capability:
        assert has_capability("admin", cap)


def test_require_capability_enforces_status_and_role():
    active_student = Principal(
        workspace_key="s1", issuer=None, subject="s1", role="student", status="active"
    )
    disabled_student = Principal(
        workspace_key="s2", issuer=None, subject="s2", role="student", status="disabled"
    )
    active_auditor = Principal(
        workspace_key="a1", issuer=None, subject="a1", role="auditor", status="active"
    )

    require_capability(active_student, Capability.READ_OWN_WORKSPACE)

    with pytest.raises(HTTPException) as exc_disabled:
        require_capability(disabled_student, Capability.READ_OWN_WORKSPACE)
    assert exc_disabled.value.status_code == 403
    assert "disabled" in exc_disabled.value.detail

    with pytest.raises(HTTPException) as exc_unauth:
        require_capability(active_student, Capability.MANAGE_USERS)
    assert exc_unauth.value.status_code == 403

    require_capability(active_auditor, Capability.READ_AUDIT)


def test_last_admin_protection(db):
    admin1 = create_user(db, role="admin", status="active")
    store = AdminStore(db, admin1)
    # Admin1 cannot demote self when sole admin
    with pytest.raises(HTTPException) as last_admin:
        store.update_user(admin1.user_id, role="student", status="active")
    assert last_admin.value.status_code == 409
    assert "last active administrator" in last_admin.value.detail.lower()

    # Create admin2
    admin2 = create_user(db, role="admin", status="active")

    # With multiple admins, admin1 trying to demote self still raises self-demote error
    with pytest.raises(HTTPException) as self_demote:
        store.update_user(admin1.user_id, role="student", status="active")
    assert self_demote.value.status_code == 409
    assert "own account" in self_demote.value.detail.lower()

    # Now admin1 can demote admin2
    store.update_user(admin2.user_id, role="student", status="active")

    # Demoting admin1 now fails again because it's the last active admin
    admin2_actor = Principal(
        workspace_key=admin2.workspace_key,
        issuer=None,
        subject=str(admin2.user_id),
        role="admin",
        user_id=admin2.user_id,
        status="active",
    )
    store2 = AdminStore(db, admin2_actor)
    with pytest.raises(HTTPException) as last_admin2:
        store2.update_user(admin1.user_id, role="student", status="active")
    assert last_admin2.value.status_code == 409
    assert "last active administrator" in last_admin2.value.detail.lower()

