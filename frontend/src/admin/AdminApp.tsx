import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { AuthSession } from '../api/types';
import type {
  AdminArtifact,
  AdminAuditEntry,
  AdminCollection,
  AdminConversation,
  AdminJob,
  AdminOverview,
  AdminSource,
  AdminTranscript,
  AdminUser,
  AdminUserSession,
} from '../api/types';
import {
  cancelAdminJob,
  createAdminCollection,
  deleteAdminCollection,
  deleteAdminConversation,
  getAdminOverview,
  getAdminTranscript,
  listAdminArtifacts,
  listAdminAudit,
  listAdminCollections,
  listAdminConversations,
  listAdminJobs,
  listAdminSources,
  listAdminUserSessions,
  listAdminUsers,
  renameAdminCollection,
  revokeAdminUserSessions,
  updateAdminUser,
} from '../api/backoffice';
import { isDemoMode } from '../api/client';
import { errorMessage } from '../lib/errors';
import { TopBar } from '../components/shell/TopBar';
import { SessionGate } from '../app/SessionGate';

type Tab = 'overview' | 'users' | 'chats' | 'jobs' | 'sources' | 'artifacts' | 'collections' | 'audit';

type Selected =
  | { kind: 'user'; value: AdminUser }
  | { kind: 'job'; value: AdminJob }
  | { kind: 'source'; value: AdminSource }
  | { kind: 'artifact'; value: AdminArtifact }
  | { kind: 'collection'; value: AdminCollection }
  | null;

const TABS: Array<[Tab, string]> = [
  ['overview', 'Overview'],
  ['users', 'Users'],
  ['chats', 'Chats'],
  ['jobs', 'Jobs'],
  ['sources', 'Sources'],
  ['artifacts', 'Artifacts'],
  ['collections', 'Collections'],
  ['audit', 'Audit'],
];

const PAGE = 50;

function when(value?: string | null): string {
  return value ? new Date(value).toLocaleString() : '—';
}

function FieldLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="admin-field-line">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

function AdminSurface({ session, onLogout }: { session: AuthSession; onLogout?: () => void }) {
  const [tab, setTab] = useState<Tab>('overview');
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [conversations, setConversations] = useState<AdminConversation[]>([]);
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [sources, setSources] = useState<AdminSource[]>([]);
  const [artifacts, setArtifacts] = useState<AdminArtifact[]>([]);
  const [collections, setCollections] = useState<AdminCollection[]>([]);
  const [audit, setAudit] = useState<AdminAuditEntry[]>([]);
  const [selected, setSelected] = useState<Selected>(null);
  const [sessions, setSessions] = useState<AdminUserSession[] | null>(null);
  const [transcript, setTranscript] = useState<AdminTranscript | null>(null);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [newCollection, setNewCollection] = useState('');
  const [editCollection, setEditCollection] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      if (tab === 'overview') setOverview(await getAdminOverview());
      if (tab === 'users') {
        setUsers(await listAdminUsers({ query: search, limit: PAGE, offset }));
      }
      if (tab === 'chats') {
        setConversations(await listAdminConversations({ query: search, limit: PAGE, offset }));
      }
      if (tab === 'jobs') setJobs(await listAdminJobs('', PAGE, offset));
      if (tab === 'sources') setSources(await listAdminSources('', PAGE, offset));
      if (tab === 'artifacts') setArtifacts(await listAdminArtifacts(PAGE, offset));
      if (tab === 'collections') setCollections(await listAdminCollections(PAGE, offset));
      if (tab === 'audit') setAudit(await listAdminAudit(200));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }, [tab, search, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  if (isDemoMode()) {
    return (
      <div className="shell">
        <TopBar session={session} onLogout={onLogout} />
        <main className="session-gate">
          <div className="gate-card">
            <h2>Admin needs the real API</h2>
            <p>Run the admin bundle with `VITE_API_MODE=http`.</p>
          </div>
        </main>
      </div>
    );
  }

  if (session.role !== 'admin') {
    return (
      <div className="shell">
        <TopBar session={session} onLogout={onLogout} />
        <main className="session-gate">
          <div className="gate-card">
            <h2>Administrator access required</h2>
            <p>This account can use the advisor but cannot manage institutional users.</p>
            <a className="primary-button" href="/">
              Return to the advisor
            </a>
          </div>
        </main>
      </div>
    );
  }

  const switchTab = (value: Tab) => {
    setTab(value);
    setOffset(0);
    setSelected(null);
    setSessions(null);
    setTranscript(null);
    setError(null);
  };

  const visible =
    tab === 'users'
      ? users
      : tab === 'chats'
        ? conversations
        : tab === 'jobs'
          ? jobs
          : tab === 'sources'
            ? sources
            : tab === 'artifacts'
              ? artifacts
              : tab === 'collections'
                ? collections
                : [];
  const hasNext = !['overview', 'audit'].includes(tab) && visible.length === PAGE;

  const openUser = async (user: AdminUser) => {
    setTranscript(null);
    setSelected({ kind: 'user', value: user });
    setSessions(null);
    try {
      setSessions(await listAdminUserSessions(user.userId));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };

  const openTranscript = async (conversation: AdminConversation) => {
    setBusy(true);
    setError(null);
    setSelected(null);
    try {
      setTranscript(await getAdminTranscript(conversation.conversationId));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const saveUser = async (user: AdminUser, role: AdminUser['role'], status: AdminUser['status']) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateAdminUser(user.userId, { role, status });
      setSelected((current) =>
        current?.kind === 'user' && current.value.userId === user.userId ? { kind: 'user', value: updated } : current,
      );
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (user: AdminUser) => {
    setBusy(true);
    setError(null);
    try {
      await revokeAdminUserSessions(user.userId);
      if (selected?.kind === 'user' && selected.value.userId === user.userId) {
        setSessions(await listAdminUserSessions(user.userId));
      }
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const cancelJob = async (job: AdminJob) => {
    if (!window.confirm(`Cancel job ${job.jobId}?`)) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await cancelAdminJob(job.jobId);
      setSelected((current) =>
        current?.kind === 'job' && current.value.jobId === job.jobId ? { kind: 'job', value: updated } : current,
      );
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const deleteConversation = async (conversation: AdminTranscript['conversation']) => {
    if (!window.confirm(`Delete “${conversation.title}” and its turn jobs? This cannot be undone.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await deleteAdminConversation(conversation.conversationId);
      setTranscript(null);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const createCollection = async () => {
    if (!newCollection.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createAdminCollection(newCollection.trim());
      setNewCollection('');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const renameCollection = async (collection: AdminCollection) => {
    if (!editCollection.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await renameAdminCollection(collection.collectionId, editCollection.trim());
      setSelected({ kind: 'collection', value: updated });
      setEditCollection(updated.name);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const deleteCollection = async (collection: AdminCollection) => {
    if (!window.confirm(`Delete empty collection “${collection.name}”?`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteAdminCollection(collection.collectionId);
      setSelected(null);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell admin-shell">
      <TopBar session={session} onLogout={onLogout} />
      <div className="surface-nav">
        <strong>Institutional Admin</strong>
        <a href="/">Main</a>
        <a href="/debug.html">Debug</a>
      </div>
      <main className="admin-main">
        <div className="admin-tabs" role="tablist">
          {TABS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={tab === value ? 'admin-tab active' : 'admin-tab'}
              onClick={() => switchTab(value)}
            >
              {label}
            </button>
          ))}
          <button type="button" className="secondary-button" onClick={() => void load()} disabled={busy}>
            Refresh
          </button>
        </div>
        {error && (
          <p role="alert" className="admin-error">
            {error}
          </p>
        )}
        {(tab === 'users' || tab === 'chats') && (
          <form
            className="admin-search"
            onSubmit={(event) => {
              event.preventDefault();
              setOffset(0);
              setSearch(query);
            }}
          >
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={tab === 'users' ? 'Search name, email, subject, or workspace' : 'Search chat title'}
              aria-label="Search"
            />
            <button type="submit" className="primary-button" disabled={busy}>
              Search
            </button>
          </form>
        )}

        {tab === 'collections' && (
          <form
            className="admin-inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              void createCollection();
            }}
          >
            <input
              value={newCollection}
              onChange={(event) => setNewCollection(event.target.value)}
              placeholder="New collection name"
              aria-label="New collection name"
            />
            <button type="submit" className="primary-button" disabled={busy || !newCollection.trim()}>
              Create collection
            </button>
          </form>
        )}

        <div className="admin-workspace">
          <div className="admin-list">
            {tab === 'overview' && overview && (
              <div className="admin-cards">
                {(
                  [
                    ['Users', overview.users],
                    ['Admins', overview.admins],
                    ['Active sessions', overview.activeSessions],
                    ['Conversations', overview.conversations],
                    ['Turns', overview.turns],
                    ['Compaction checkpoints', overview.summaries],
                    ['Queued jobs', overview.queuedJobs],
                    ['Running jobs', overview.runningJobs],
                    ['Failed jobs', overview.failedJobs],
                    ['Sources', overview.sources],
                    ['Artifacts', overview.artifacts],
                    ['Retrieval traces', overview.traces],
                  ] as Array<[string, number]>
                ).map(([label, value]) => (
                  <div className="admin-card" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
            )}

            {tab === 'users' && (
              <div>
                <p className="admin-sub">
                  Users appear after their first verified login; roles and status are managed here.
                </p>
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
                      {users.map((user) => (
                        <UserRow
                          key={user.userId}
                          user={user}
                          busy={busy}
                          onOpen={() => void openUser(user)}
                          onSave={saveUser}
                          onRevoke={revoke}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {tab === 'chats' && (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Workspace</th>
                      <th>Turns</th>
                      <th>Checkpoints</th>
                      <th>Updated</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {conversations.map((conversation) => (
                      <tr key={conversation.conversationId}>
                        <td>{conversation.title}</td>
                        <td className="mono">{conversation.workspaceKey}</td>
                        <td>{conversation.turnCount}</td>
                        <td>{conversation.summaryCount}</td>
                        <td>{when(conversation.updatedAt)}</td>
                        <td>
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() => void openTranscript(conversation)}
                          >
                            Open (audited)
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === 'jobs' && (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Kind</th>
                      <th>Status</th>
                      <th>Scope</th>
                      <th>Attempts</th>
                      <th>Created</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((job) => (
                      <tr
                        key={job.jobId}
                        className="admin-clickable"
                        onClick={() => setSelected({ kind: 'job', value: job })}
                      >
                        <td>{job.kind}</td>
                        <td>
                          {job.status}
                          {job.cancelRequested && job.status === 'running' ? ' (cancel requested)' : ''}
                        </td>
                        <td className="mono">{job.scopeKey}</td>
                        <td>
                          {job.attemptCount}/{job.maxAttempts}
                        </td>
                        <td>{when(job.createdAt)}</td>
                        <td>
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setSelected({ kind: 'job', value: job });
                            }}
                          >
                            View
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === 'sources' && (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Filename</th>
                      <th>Workspace</th>
                      <th>Status</th>
                      <th>Current</th>
                      <th>Revision</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((source) => (
                      <tr
                        key={source.sourceId}
                        className="admin-clickable"
                        onClick={() => setSelected({ kind: 'source', value: source })}
                      >
                        <td>{source.filename}</td>
                        <td className="mono">{source.workspaceKey || 'shared'}</td>
                        <td>{source.status}</td>
                        <td>{source.isCurrent ? 'yes' : 'no'}</td>
                        <td>{source.indexRevision}</td>
                        <td>{when(source.updatedAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === 'artifacts' && (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Kind</th>
                      <th>Workspace</th>
                      <th>Revisions</th>
                      <th>Original file</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {artifacts.map((artifact) => (
                      <tr key={artifact.artifactId} onClick={() => setSelected({ kind: 'artifact', value: artifact })}>
                        <td>{artifact.kind}</td>
                        <td className="mono">{artifact.workspaceKey}</td>
                        <td>{artifact.revisionCount}</td>
                        <td>{artifact.originalFilename || '—'}</td>
                        <td>{when(artifact.createdAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === 'collections' && (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Sources</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {collections.map((collection) => (
                      <tr
                        key={collection.collectionId}
                        className="admin-clickable"
                        onClick={() => {
                          setSelected({ kind: 'collection', value: collection });
                          setEditCollection(collection.name);
                        }}
                      >
                        <td>{collection.name}</td>
                        <td>{collection.sourceCount}</td>
                        <td>{when(collection.createdAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === 'audit' && (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Action</th>
                      <th>Actor</th>
                      <th>Target</th>
                      <th>Detail</th>
                      <th>When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.map((entry) => (
                      <tr key={entry.auditId}>
                        <td>{entry.action}</td>
                        <td>{entry.actorEmail || entry.actorDisplayName || entry.actorWorkspace}</td>
                        <td className="mono">
                          {entry.targetType}:{entry.targetId || '—'}
                        </td>
                        <td className="mono admin-detail">{JSON.stringify(entry.detail)}</td>
                        <td>{when(entry.createdAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {!['overview', 'audit'].includes(tab) && (
              <div className="admin-pagination">
                <span>{visible.length ? `${offset + 1}–${offset + visible.length}` : 'No rows'}</span>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || !hasNext}
                  onClick={() => setOffset(offset + PAGE)}
                >
                  Next
                </button>
              </div>
            )}
          </div>

          {(selected || transcript) && (
            <aside className="admin-detail-panel" aria-label="Admin detail">
              {transcript && (
                <>
                  <div className="admin-detail-head">
                    <h2>{transcript.conversation.title}</h2>
                    <button type="button" className="secondary-button" onClick={() => setTranscript(null)}>
                      Close
                    </button>
                  </div>
                  <FieldLine label="Workspace">
                    <span className="mono">{transcript.conversation.workspaceKey}</span>
                  </FieldLine>
                  <FieldLine label="Turns">{transcript.turns.length}</FieldLine>
                  <FieldLine label="Updated">{when(transcript.conversation.updatedAt)}</FieldLine>
                  <button
                    type="button"
                    className="danger-button"
                    disabled={busy}
                    onClick={() => void deleteConversation(transcript.conversation)}
                  >
                    Delete conversation
                  </button>
                  <div className="admin-transcript">
                    {transcript.turns.map((turn) => (
                      <article key={`${turn.sequence}-${turn.role}`} className="admin-turn">
                        <header>
                          Turn {turn.sequence} · {turn.role} · {turn.status}
                        </header>
                        <p>{turn.text}</p>
                      </article>
                    ))}
                  </div>
                </>
              )}

              {selected?.kind === 'user' && (
                <>
                  <div className="admin-detail-head">
                    <h2>{selected.value.displayName || selected.value.email || 'User'}</h2>
                    <button type="button" className="secondary-button" onClick={() => setSelected(null)}>
                      Close
                    </button>
                  </div>
                  <FieldLine label="Email">{selected.value.email || '—'}</FieldLine>
                  <FieldLine label="Role">{selected.value.role}</FieldLine>
                  <FieldLine label="Status">{selected.value.status}</FieldLine>
                  <FieldLine label="Workspace">
                    <span className="mono">{selected.value.workspaceKey}</span>
                  </FieldLine>
                  <FieldLine label="Issuer">
                    <span className="mono">{selected.value.issuer}</span>
                  </FieldLine>
                  <FieldLine label="Subject">
                    <span className="mono">{selected.value.subject}</span>
                  </FieldLine>
                  <FieldLine label="Chats">{selected.value.conversationCount}</FieldLine>
                  <FieldLine label="Last seen">{when(selected.value.lastSeenAt)}</FieldLine>
                  <h3>Sessions</h3>
                  {sessions === null && <p className="admin-sub">Loading…</p>}
                  {sessions?.length === 0 && <p className="admin-sub">No sessions.</p>}
                  {sessions?.map((value, index) => (
                    <div className="admin-session" key={`${value.createdAt}-${index}`}>
                      <span>{when(value.createdAt)}</span>
                      <span>expires {when(value.expiresAt)}</span>
                      {value.revokedAt && <strong>revoked</strong>}
                    </div>
                  ))}
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => void revoke(selected.value)}
                  >
                    Revoke all sessions
                  </button>
                </>
              )}

              {selected?.kind === 'job' && (
                <>
                  <div className="admin-detail-head">
                    <h2>Job</h2>
                    <button type="button" className="secondary-button" onClick={() => setSelected(null)}>
                      Close
                    </button>
                  </div>
                  <FieldLine label="Job ID">
                    <span className="mono">{selected.value.jobId}</span>
                  </FieldLine>
                  <FieldLine label="Kind">{selected.value.kind}</FieldLine>
                  <FieldLine label="Status">
                    {selected.value.status}
                    {selected.value.cancelRequested ? ' (cancel requested)' : ''}
                  </FieldLine>
                  <FieldLine label="Scope">
                    <span className="mono">{selected.value.scopeKey}</span>
                  </FieldLine>
                  <FieldLine label="Attempts">
                    {selected.value.attemptCount}/{selected.value.maxAttempts}
                  </FieldLine>
                  <FieldLine label="Created">{when(selected.value.createdAt)}</FieldLine>
                  <FieldLine label="Finished">{when(selected.value.finishedAt)}</FieldLine>
                  {selected.value.error && (
                    <pre className="admin-json">{JSON.stringify(selected.value.error, null, 2)}</pre>
                  )}
                  {['queued', 'running'].includes(selected.value.status) && (
                    <button
                      type="button"
                      className="danger-button"
                      disabled={busy || selected.value.cancelRequested}
                      onClick={() => void cancelJob(selected.value)}
                    >
                      {selected.value.cancelRequested ? 'Cancel requested' : 'Cancel job'}
                    </button>
                  )}
                </>
              )}

              {selected?.kind === 'source' && (
                <>
                  <div className="admin-detail-head">
                    <h2>{selected.value.filename}</h2>
                    <button type="button" className="secondary-button" onClick={() => setSelected(null)}>
                      Close
                    </button>
                  </div>
                  <FieldLine label="Status">{selected.value.status}</FieldLine>
                  <FieldLine label="Workspace">
                    <span className="mono">{selected.value.workspaceKey || 'shared'}</span>
                  </FieldLine>
                  <FieldLine label="Collection">
                    <span className="mono">{selected.value.collectionId}</span>
                  </FieldLine>
                  <FieldLine label="Content type">{selected.value.contentType}</FieldLine>
                  <FieldLine label="Index revision">{selected.value.indexRevision}</FieldLine>
                  <FieldLine label="Current">{selected.value.isCurrent ? 'yes' : 'no'}</FieldLine>
                  <FieldLine label="Created">{when(selected.value.createdAt)}</FieldLine>
                  <p className="admin-sub">
                    Source deletion stays in ingest tooling because it also owns raw-file cleanup.
                  </p>
                </>
              )}

              {selected?.kind === 'artifact' && (
                <>
                  <div className="admin-detail-head">
                    <h2>{selected.value.kind}</h2>
                    <button type="button" className="secondary-button" onClick={() => setSelected(null)}>
                      Close
                    </button>
                  </div>
                  <FieldLine label="Artifact">
                    <span className="mono">{selected.value.artifactId}</span>
                  </FieldLine>
                  <FieldLine label="Head revision">
                    <span className="mono">{selected.value.headRevisionId}</span>
                  </FieldLine>
                  <FieldLine label="Workspace">
                    <span className="mono">{selected.value.workspaceKey}</span>
                  </FieldLine>
                  <FieldLine label="Revisions">{selected.value.revisionCount}</FieldLine>
                  <FieldLine label="Original file">{selected.value.originalFilename || '—'}</FieldLine>
                  <FieldLine label="Created">{when(selected.value.createdAt)}</FieldLine>
                  <p className="admin-sub">Artifact revision deletion stays in the artifact revision workflow.</p>
                </>
              )}

              {selected?.kind === 'collection' && (
                <>
                  <div className="admin-detail-head">
                    <h2>{selected.value.name}</h2>
                    <button type="button" className="secondary-button" onClick={() => setSelected(null)}>
                      Close
                    </button>
                  </div>
                  <FieldLine label="Collection">
                    <span className="mono">{selected.value.collectionId}</span>
                  </FieldLine>
                  <FieldLine label="Sources">{selected.value.sourceCount}</FieldLine>
                  <FieldLine label="Created">{when(selected.value.createdAt)}</FieldLine>
                  <label className="admin-field-line">
                    <span>Name</span>
                    <input value={editCollection} onChange={(event) => setEditCollection(event.target.value)} />
                  </label>
                  <div className="admin-actions">
                    <button
                      type="button"
                      className="primary-button"
                      disabled={busy || !editCollection.trim()}
                      onClick={() => void renameCollection(selected.value)}
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      disabled={busy || selected.value.sourceCount > 0}
                      onClick={() => void deleteCollection(selected.value)}
                      title={selected.value.sourceCount ? 'Only empty collections can be deleted' : undefined}
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </aside>
          )}
        </div>
      </main>
    </div>
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
          <option value="admin">admin</option>
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
      <td>{when(user.lastSeenAt)}</td>
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

export function AdminApp() {
  return <SessionGate render={(session, logout) => <AdminSurface session={session} onLogout={logout} />} />;
}
