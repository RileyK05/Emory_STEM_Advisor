import { useEffect, useState } from 'react';
import type { AdminUser, AdminUserSession } from '../api/types';
import { listAdminUserSessions, listAdminUsers, revokeAdminUserSessions, updateAdminUser } from '../api/backoffice';
import { errorMessage } from '../lib/errors';
import { formatTimestamp } from '../lib/format';
import {
  AdminError,
  AdminSearch,
  AdminWorkspace,
  DetailHead,
  FieldLine,
  PAGE,
  Pager,
  RefreshButton,
  usePagedList,
  type AdminPanelProps,
} from './adminUi';

export function UsersPanel({ toolbar }: AdminPanelProps) {
  const [search, setSearch] = useState('');
  const list = usePagedList((offset) => listAdminUsers({ query: search, limit: PAGE, offset }), [search]);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [sessions, setSessions] = useState<AdminUserSession[] | null>(null);

  const openUser = async (user: AdminUser) => {
    setSelected(user);
    setSessions(null);
    try {
      setSessions(await listAdminUserSessions(user.userId));
    } catch (cause) {
      list.setError(errorMessage(cause));
    }
  };

  const saveUser = (user: AdminUser, role: AdminUser['role'], status: AdminUser['status']) =>
    list.run(async () => {
      const updated = await updateAdminUser(user.userId, { role, status });
      setSelected((current) => (current?.userId === user.userId ? updated : current));
      await list.reload();
    });

  const revoke = (user: AdminUser) =>
    list.run(async () => {
      await revokeAdminUserSessions(user.userId);
      if (selected?.userId === user.userId) setSessions(await listAdminUserSessions(user.userId));
      await list.reload();
    });

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={list.working} onRefresh={() => void list.reload()} />
      <AdminError error={list.error} />
      <AdminSearch
        placeholder="Search name, email, subject, or workspace"
        busy={list.working}
        onSearch={(query) => {
          list.setOffset(0);
          setSearch(query);
        }}
      />
      <AdminWorkspace
        detail={
          selected && (
            <UserDetail
              user={selected}
              sessions={sessions}
              busy={list.working}
              onClose={() => setSelected(null)}
              onRevoke={() => void revoke(selected)}
            />
          )
        }
      >
        <div>
          <p className="admin-sub">Users appear after their first verified login; roles and status are managed here.</p>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Workspace</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Chats</th>
                  <th>Sessions</th>
                  <th>Last seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.rows.map((user) => (
                  <UserRow
                    key={user.userId}
                    user={user}
                    busy={list.working}
                    onOpen={() => void openUser(user)}
                    onSave={saveUser}
                    onRevoke={revoke}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <Pager offset={list.offset} count={list.rows.length} busy={list.working} onOffset={list.setOffset} />
      </AdminWorkspace>
    </>
  );
}

function UserDetail({
  user,
  sessions,
  busy,
  onClose,
  onRevoke,
}: {
  user: AdminUser;
  sessions: AdminUserSession[] | null;
  busy: boolean;
  onClose: () => void;
  onRevoke: () => void;
}) {
  return (
    <>
      <DetailHead title={user.displayName || user.email || 'User'} onClose={onClose} />
      <FieldLine label="Email">{user.email || '—'}</FieldLine>
      <FieldLine label="Role">{user.role}</FieldLine>
      <FieldLine label="Status">{user.status}</FieldLine>
      <FieldLine label="Workspace">
        <span className="mono">{user.workspaceKey}</span>
      </FieldLine>
      <FieldLine label="Issuer">
        <span className="mono">{user.issuer}</span>
      </FieldLine>
      <FieldLine label="Subject">
        <span className="mono">{user.subject}</span>
      </FieldLine>
      <FieldLine label="Chats">{user.conversationCount}</FieldLine>
      <FieldLine label="Last seen">{formatTimestamp(user.lastSeenAt)}</FieldLine>
      <h3>Sessions</h3>
      {sessions === null && <p className="admin-sub">Loading…</p>}
      {sessions?.length === 0 && <p className="admin-sub">No sessions.</p>}
      {sessions?.map((value, index) => (
        <div className="admin-session" key={`${value.createdAt}-${index}`}>
          <span>{formatTimestamp(value.createdAt)}</span>
          <span>expires {formatTimestamp(value.expiresAt)}</span>
          {value.revokedAt && <strong>revoked</strong>}
        </div>
      ))}
      <button type="button" className="secondary-button" disabled={busy} onClick={onRevoke}>
        Revoke all sessions
      </button>
    </>
  );
}

function UserRow({
  user,
  busy,
  onOpen,
  onSave,
  onRevoke,
}: {
  user: AdminUser;
  busy: boolean;
  onOpen: () => void;
  onSave: (user: AdminUser, role: AdminUser['role'], status: AdminUser['status']) => Promise<void>;
  onRevoke: (user: AdminUser) => Promise<void>;
}) {
  const [role, setRole] = useState(user.role);
  const [status, setStatus] = useState(user.status);
  useEffect(() => {
    setRole(user.role);
    setStatus(user.status);
  }, [user.role, user.status]);
  const dirty = role !== user.role || status !== user.status;
  return (
    <tr className="admin-clickable" onClick={onOpen}>
      <td>
        <strong>{user.displayName || user.email || user.subject}</strong>
        <div className="admin-sub">{user.email || '—'}</div>
      </td>
      <td className="mono">{user.workspaceKey}</td>
      <td onClick={(event) => event.stopPropagation()}>
        <select value={role} onChange={(event) => setRole(event.target.value as AdminUser['role'])}>
          <option value="student">student</option>
          <option value="advisor">advisor</option>
          <option value="admin">admin</option>
          <option value="auditor">auditor</option>
        </select>
      </td>
      <td onClick={(event) => event.stopPropagation()}>
        <select value={status} onChange={(event) => setStatus(event.target.value as AdminUser['status'])}>
          <option value="active">active</option>
          <option value="disabled">disabled</option>
        </select>
      </td>
      <td>{user.conversationCount}</td>
      <td>{user.activeSessions}</td>
      <td>{formatTimestamp(user.lastSeenAt)}</td>
      <td className="admin-actions" onClick={(event) => event.stopPropagation()}>
        <button
          type="button"
          className="primary-button"
          disabled={busy || !dirty}
          onClick={() => void onSave(user, role, status)}
        >
          Save
        </button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void onRevoke(user)}>
          Revoke
        </button>
      </td>
    </tr>
  );
}
