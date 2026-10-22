import { useState } from 'react';
import type { AdminAuditEntry } from '../api/types';
import { exportAdminAuditUrl, listAdminAudit } from '../api/backoffice';
import type { AdminAuditParams } from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
import { useLoad } from '../lib/useLoad';
import { AdminError, AdminWorkspace, PAGE, RefreshButton, type AdminPanelProps } from './adminUi';

/** The filter form's draft; nothing is queried until it is applied. */
const NO_FILTERS = { action: '', actor: '', targetType: '', targetId: '', since: '', until: '' };
type AuditFilters = typeof NO_FILTERS;

/** Draft text fields -> query params; local datetime inputs become UTC instants. */
function toParams(filters: AuditFilters): AdminAuditParams {
  return {
    action: filters.action || undefined,
    actor: filters.actor || undefined,
    targetType: filters.targetType || undefined,
    targetId: filters.targetId || undefined,
    since: filters.since ? new Date(filters.since).toISOString() : undefined,
    until: filters.until ? new Date(filters.until).toISOString() : undefined,
  };
}

export function AuditPanel({ toolbar }: AdminPanelProps) {
  const [draft, setDraft] = useState<AuditFilters>(NO_FILTERS);
  const [applied, setApplied] = useState<AdminAuditParams>({});
  const [cursor, setCursor] = useState('');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [entries, setEntries] = useState<AdminAuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { loading, reload } = useLoad(
    () => listAdminAudit({ ...applied, cursor: cursor || undefined, limit: PAGE }),
    [applied, cursor],
    {
      onLoad: (page) => {
        setEntries(page.entries);
        setNextCursor(page.nextCursor);
      },
      setError,
    },
  );

  const edit = (field: keyof AuditFilters) => (event: { target: { value: string } }) =>
    setDraft((current) => ({ ...current, [field]: event.target.value }));

  const apply = () => {
    if (draft.since && draft.until && new Date(draft.since) > new Date(draft.until)) {
      setError('The start of the audit range must precede its end.');
      return;
    }
    setCursor('');
    setApplied(toParams(draft));
  };

  const reset = () => {
    setDraft(NO_FILTERS);
    setApplied({});
    setCursor('');
  };

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={loading} onRefresh={() => void reload()} />
      <AdminError error={error} />
      <AdminWorkspace>
        <div>
          <form
            className="admin-search"
            onSubmit={(event) => {
              event.preventDefault();
              apply();
            }}
            style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}
          >
            <input
              value={draft.action}
              onChange={edit('action')}
              placeholder="Filter by action (e.g. user.role_status)"
              aria-label="Filter action"
              style={{ flex: '1 1 180px' }}
            />
            <input
              value={draft.actor}
              onChange={edit('actor')}
              placeholder="Filter by actor (email, workspace)"
              aria-label="Filter actor"
              style={{ flex: '1 1 180px' }}
            />
            <input
              value={draft.targetType}
              onChange={edit('targetType')}
              placeholder="Target type (e.g. user, conversation)"
              aria-label="Filter target type"
              style={{ flex: '1 1 140px' }}
            />
            <input
              value={draft.targetId}
              onChange={edit('targetId')}
              placeholder="Target ID"
              aria-label="Filter target ID"
              style={{ flex: '1 1 140px' }}
            />
            <label>
              From (local time)
              <input type="datetime-local" aria-label="Filter from date" value={draft.since} onChange={edit('since')} />
            </label>
            <label>
              Until (local time)
              <input
                type="datetime-local"
                aria-label="Filter until date"
                value={draft.until}
                onChange={edit('until')}
              />
            </label>
            <button type="submit" className="secondary-button" disabled={loading}>
              Apply filters
            </button>
            <button type="button" className="secondary-button" disabled={loading} onClick={reset}>
              Reset
            </button>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem' }}>
              <a className="secondary-button" href={exportAdminAuditUrl({ format: 'csv', ...applied })} download>
                Export CSV
              </a>
              <a className="secondary-button" href={exportAdminAuditUrl({ format: 'json', ...applied })} download>
                Export JSON
              </a>
            </div>
          </form>

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
                {entries.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: 'center', padding: '2rem' }}>
                      {loading ? 'Loading audit logs...' : 'No audit records match the current filters.'}
                    </td>
                  </tr>
                ) : (
                  entries.map((entry) => (
                    <tr key={entry.auditId}>
                      <td>{entry.action}</td>
                      <td>{entry.actorEmail || entry.actorDisplayName || entry.actorWorkspace}</td>
                      <td className="mono">
                        {entry.targetType}:{entry.targetId || '—'}
                      </td>
                      <td className="mono admin-detail">{JSON.stringify(entry.detail)}</td>
                      <td>{formatTimestamp(entry.createdAt)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="admin-pagination">
            <span>{entries.length ? `Showing ${entries.length} records` : 'No rows'}</span>
            {cursor && (
              <button type="button" className="secondary-button" disabled={loading} onClick={() => setCursor('')}>
                First page
              </button>
            )}
            <button
              type="button"
              className="secondary-button"
              disabled={loading || !nextCursor}
              onClick={() => {
                if (nextCursor) setCursor(nextCursor);
              }}
            >
              Next page
            </button>
          </div>
        </div>
      </AdminWorkspace>
    </>
  );
}
