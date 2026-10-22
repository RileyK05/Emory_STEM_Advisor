"""Tests for student privacy boundaries, durable roles, and access grants (Item 15)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.admin.store import AdminStore
from app.auth.capabilities import (
    Capability,
    has_capability,
    require_capability,
)
from app.auth.models import Principal
from app.conversations.store import ConversationStore
from app.db import queries


def _create_user(db, *, role="student", status="active", workspace=None, email=None):
    user_id = uuid4()
    workspace_key = workspace or f"ws-{user_id}"
    email_addr = email or f"{user_id}@emory.edu"
    db.execute(
        queries.get("upsert_app_user"),
        {
            "user_id": user_id,
            "issuer": "https://auth.emory.edu",
            "subject": str(user_id),
            "workspace_key": workspace_key,
            "display_name": f"User {user_id}",
            "email": email_addr,
            "bootstrap": False,
        },
    ).fetchone()
    # Update role and status
    db.execute(
        queries.get("admin_update_user"),
        {"user_id": user_id, "role": role, "status": status},
    )
    db.commit()
    return Principal(
        workspace_key=workspace_key,
        issuer="https://auth.emory.edu",
        subject=str(user_id),
        display_name=f"User {user_id}",
        email=email_addr,
        role=role,
        user_id=user_id,
        status=status,
    )


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
    admin1 = _create_user(db, role="admin", status="active")
    store = AdminStore(db, admin1)
    # Admin1 cannot demote self when sole admin
    with pytest.raises(HTTPException) as last_admin:
        store.update_user(admin1.user_id, role="student", status="active")
    assert last_admin.value.status_code == 409
    assert "last active administrator" in last_admin.value.detail.lower()

    # Create admin2
    admin2 = _create_user(db, role="admin", status="active")

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


def test_admin_transcript_read_requires_grant_for_cross_workspace(db):
    student = _create_user(db, role="student", status="active")
    admin1 = _create_user(db, role="admin", status="active")

    # Student creates conversation with turns
    conv_store = ConversationStore(db, student.workspace_key)
    conv = conv_store.create("Private Math Plan")
    conv_id = UUID(conv["conversationId"])
    req_id = uuid4()
    conv_store.begin(conv_id, req_id, 0, {"query": "Hello Math"})
    conv_store.finish(
        conv_id,
        req_id,
        {"type": "answer", "answer": "Math is good.", "citations": [], "trace": {}},
    )

    admin_store = AdminStore(db, admin1)

    # Admin attempting to read student's transcript without a grant raises 403
    with pytest.raises(HTTPException) as ungranted:
        admin_store.transcript(conv_id)
    assert ungranted.value.status_code == 403
    assert "support grant" in ungranted.value.detail.lower()

    # Check that access.denied was audited
    audit_rows, _ = admin_store.audit_log(action="access.denied")
    assert any(
        row["target_id"] == str(conv_id) and row["action"] == "access.denied"
        for row in audit_rows
    )


def test_grant_lifecycle_and_expiry_revocation(db):
    student = _create_user(db, role="student", status="active")
    admin_approver = _create_user(db, role="admin", status="active")
    admin_support = _create_user(db, role="admin", status="active")

    conv_store = ConversationStore(db, student.workspace_key)
    conv = conv_store.create("Student Chemistry Plan")
    conv_id = UUID(conv["conversationId"])

    approver_store = AdminStore(db, admin_approver)
    support_store = AdminStore(db, admin_support)

    # 1. Admin cannot self-approve support grant
    with pytest.raises(HTTPException) as self_app:
        support_store.create_grant(
            grant_type="support_access",
            actor_user_id=admin_support.user_id,
            target_workspace_key=student.workspace_key,
            resource_type="conversation",
            resource_id=str(conv_id),
            reason="Investigating student issue",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    assert self_app.value.status_code == 409
    assert "cannot self-approve" in self_app.value.detail.lower()

    # 2. Approver creates support grant for support admin
    with pytest.raises(HTTPException) as wrong_recipient:
        approver_store.create_grant(
            grant_type="advisor_access", actor_user_id=student.user_id,
            target_workspace_key=admin_support.workspace_key, resource_type="all_records",
            reason="Incorrect recipient must be rejected",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    assert wrong_recipient.value.status_code == 422
    grant = approver_store.create_grant(
        grant_type="support_access",
        actor_user_id=admin_support.user_id,
        target_workspace_key=student.workspace_key,
        resource_type="conversation",
        resource_id=str(conv_id),
        reason="Approved investigation of ticket #1234",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    grant_id = UUID(str(grant["grant_id"]))
    assert grant["actor_user_id"] == admin_support.user_id

    # 3. Support admin can now read transcript
    transcript = support_store.transcript(conv_id)
    assert transcript["conversation"]["title"] == "Student Chemistry Plan"

    # 4. Revoking the grant blocks access again
    approver_store.revoke_grant(grant_id)

    with pytest.raises(HTTPException) as revoked_access:
        support_store.transcript(conv_id)
    assert revoked_access.value.status_code == 403


def test_grant_expired_access_denied(db):
    student = _create_user(db, role="student", status="active")
    approver = _create_user(db, role="admin", status="active")
    support = _create_user(db, role="admin", status="active")

    conv_store = ConversationStore(db, student.workspace_key)
    conv = conv_store.create("Expired Grant Test")
    conv_id = UUID(conv["conversationId"])

    approver_store = AdminStore(db, approver)
    support_store = AdminStore(db, support)

    # Reject expired new grants; model an existing grant that has since expired.
    grant = approver_store.create_grant(
        grant_type="support_access",
        actor_user_id=support.user_id,
        target_workspace_key=student.workspace_key,
        resource_type="conversation",
        resource_id=str(conv_id),
        reason="Short expired grant",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.execute("UPDATE resource_grants SET expires_at = now() - interval '5 minutes' "
               "WHERE grant_id = %s", (grant["grant_id"],))
    db.commit()

    with pytest.raises(HTTPException) as expired:
        support_store.transcript(conv_id)
    assert expired.value.status_code == 403


def test_http_grant_scope_denies_cross_owner_reads_downloads_and_publication(
    db, collection, monkeypatch,
):
    """A capability or a supplied workspace key is not authorization.

    After approval, revoking or expiring the grant denies the next read,
    direct download, and in-flight publication.
    """
    from app.auth.dependencies import get_principal
    from tests.helpers import api_client

    student = _create_user(db, role="student")
    other = _create_user(db, role="student")
    advisor = _create_user(db, role="advisor")
    requester = _create_user(db, role="admin")
    approver = _create_user(db, role="admin")
    auditor = _create_user(db, role="auditor")

    conv_store = ConversationStore(db, student.workspace_key)
    conv = conv_store.create("Private record conversation")
    conv_id = UUID(conv["conversationId"])
    request_id = uuid4()
    conv_store.begin(conv_id, request_id, 0, {"query": "grades"})
    conv_store.finish(
        conv_id, request_id,
        {"type": "answer", "answer": "See the catalog.", "citations": [], "trace": {}},
    )

    client = api_client(db, collection)

    def act(principal: Principal) -> None:
        client.app.dependency_overrides[get_principal] = lambda: principal
        monkeypatch.setattr(
            client.app.state.auth_service, "resolve", lambda *args, **kwargs: principal,
        )

    act(advisor)
    cross_read = client.get("/api/academic-record", params={"workspace_key": student.workspace_key})
    assert cross_read.status_code == 403
    verify = client.post(
        f"/api/academic-record/attempts/{uuid4()}/verify",
        json={"workspaceKey": student.workspace_key, "method": "manual_staff"},
    )
    assert verify.status_code == 403

    act(other)
    supplied_key = client.get("/api/academic-record", params={"workspace_key": student.workspace_key})
    assert supplied_key.status_code == 403

    act(auditor)
    assert client.post("/api/admin/grant-requests", json={
        "targetWorkspaceKey": student.workspace_key,
        "resourceType": "conversation",
        "resourceId": str(conv_id),
        "reason": "Auditor must not request access",
        "expiresAt": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    }).status_code == 403
    assert client.post("/api/admin/grants", json={
        "grantType": "support_access",
        "actorUserId": str(requester.user_id),
        "targetWorkspaceKey": student.workspace_key,
        "resourceType": "conversation",
        "resourceId": str(conv_id),
        "reason": "Auditor must not approve access",
        "expiresAt": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    }).status_code == 403

    expires_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    request_body = {
        "targetWorkspaceKey": student.workspace_key,
        "resourceType": "conversation",
        "resourceId": str(conv_id),
        "reason": "Investigating ticket 1234",
        "expiresAt": expires_at,
    }
    act(requester)
    created = client.post("/api/admin/grant-requests", json=request_body)
    assert created.status_code == 201, created.text
    request_id_value = created.json()["requestId"]
    assert created.json()["revision"] == 1

    stale = client.patch(f"/api/admin/grant-requests/{request_id_value}", json={
        **request_body,
        "reason": "Investigating ticket 1234 updated",
        "expectedRevision": 9,
    })
    assert stale.status_code == 409
    unchanged = client.get("/api/admin/grant-requests")
    assert unchanged.status_code == 200
    assert unchanged.json()[0]["reason"] == "Investigating ticket 1234"
    assert unchanged.json()[0]["status"] == "pending"

    edited = client.patch(f"/api/admin/grant-requests/{request_id_value}", json={
        **request_body,
        "reason": "Investigating ticket 1234 updated",
        "expectedRevision": 1,
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2

    conflicted = client.post(
        f"/api/admin/grant-requests/{request_id_value}/approve",
        json={"expectedRevision": 1},
    )
    assert conflicted.status_code == 409
    still_pending = client.get("/api/admin/grant-requests?status=pending")
    assert still_pending.json()[0]["status"] == "pending"
    assert still_pending.json()[0]["reason"] == "Investigating ticket 1234 updated"

    self_approved = client.post(
        f"/api/admin/grant-requests/{request_id_value}/approve",
        json={"expectedRevision": 2},
    )
    assert self_approved.status_code == 409

    act(approver)
    approved = client.post(
        f"/api/admin/grant-requests/{request_id_value}/approve",
        json={"expectedRevision": 2},
    )
    assert approved.status_code == 200, approved.text
    grant_id = approved.json()["grantId"]

    act(requester)
    granted_read = client.get(f"/api/admin/conversations/{conv_id}/transcript")
    assert granted_read.status_code == 200
    granted_download = client.get(f"/api/admin/conversations/{conv_id}/transcript/download")
    assert granted_download.status_code == 200
    assert "See the catalog." in granted_download.text
    staged = client.post(f"/api/admin/conversations/{conv_id}/granted-results")
    assert staged.status_code == 201, staged.text
    staging_id = staged.json()["stagingId"]

    act(approver)
    revoked = client.post(f"/api/admin/grants/{grant_id}/revoke")
    assert revoked.status_code == 200, revoked.text

    act(requester)
    assert client.get(f"/api/admin/conversations/{conv_id}/transcript").status_code == 403
    assert client.get(f"/api/admin/conversations/{conv_id}/transcript/download").status_code == 403
    withheld = client.post(f"/api/admin/granted-results/{staging_id}/publish")
    assert withheld.status_code == 403

    # A second grant can be staged, then expiry withholds the same three paths.
    act(requester)
    second = client.post("/api/admin/grant-requests", json=request_body)
    assert second.status_code == 201, second.text
    act(approver)
    second_approved = client.post(
        f"/api/admin/grant-requests/{second.json()['requestId']}/approve",
        json={"expectedRevision": second.json()["revision"]},
    )
    assert second_approved.status_code == 200, second_approved.text
    act(requester)
    second_staged = client.post(f"/api/admin/conversations/{conv_id}/granted-results")
    assert second_staged.status_code == 201, second_staged.text
    db.execute(
        "UPDATE resource_grants SET expires_at = now() - interval '5 minutes' WHERE grant_id = %s",
        (second_approved.json()["grantId"],),
    )
    db.commit()
    assert client.get(f"/api/admin/conversations/{conv_id}/transcript").status_code == 403
    assert client.get(f"/api/admin/conversations/{conv_id}/transcript/download").status_code == 403
    expired_publish = client.post(
        f"/api/admin/granted-results/{second_staged.json()['stagingId']}/publish",
    )
    assert expired_publish.status_code == 403
