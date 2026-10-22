import { useCallback, useEffect, useRef, useState } from 'react';
import type { GrantResourceType, ResourceGrant, SupportGrantRequest } from '../api/types';
import {
  approveSupportGrantRequest,
  createSupportGrantRequest,
  listResourceGrants,
  listSupportGrantRequests,
  revokeResourceGrant,
  updateSupportGrantRequest,
} from '../api/backoffice';
import { errorMessage } from '../lib/errors';

const EMPTY_FORM = {
  targetWorkspaceKey: '',
  resourceType: 'conversation' as GrantResourceType,
  resourceId: '',
  reason: '',
  expiresAt: '',
};

export function GrantsPanel() {
  const [requests, setRequests] = useState<SupportGrantRequest[]>([]);
  const [grants, setGrants] = useState<ResourceGrant[]>([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [requestId, setRequestId] = useState('');
  const [revision, setRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const [requestRows, grantRows] = await Promise.all([listSupportGrantRequests(), listResourceGrants()]);
      if (seq !== requestSeq.current) return;
      setRequests(requestRows);
      setGrants(grantRows);
    } catch (cause) {
      if (seq === requestSeq.current) setError(errorMessage(cause));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      requestSeq.current += 1;
    };
  }, [load]);

  const payload = () => ({
    targetWorkspaceKey: form.targetWorkspaceKey.trim(),
    resourceType: form.resourceType,
    resourceId: form.resourceId.trim() || null,
    reason: form.reason.trim(),
    expiresAt: new Date(form.expiresAt).toISOString(),
  });

  const submitRequest = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row =
        requestId && revision != null
          ? await updateSupportGrantRequest(requestId, { ...payload(), expectedRevision: revision })
          : await createSupportGrantRequest(payload());
      setRequestId(row.requestId);
      setRevision(row.revision);
      setNotice(row.status === 'pending' ? 'Support grant request saved.' : 'Support grant request updated.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!requestId || revision == null) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await approveSupportGrantRequest(requestId, { expectedRevision: revision });
      setRevision(row.revision);
      setNotice('Support grant approved.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (grantId: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await revokeResourceGrant(grantId);
      setNotice('Support grant revoked.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const disabled = busy || loading;
  const activeGrants = grants.filter((grant) => !grant.revokedAt);

  return (
    <section aria-label="Support grant controls">
      <h2>Support grants</h2>
      <p>
        One administrator requests read-only access. A different administrator approves it. Revoking or expiry denies
        later reads, downloads, and publication of results staged under that grant.
      </p>
      <button type="button" className="secondary-button" disabled={disabled} onClick={() => void load()}>
        Refresh grants
      </button>
      {error && (
        <div className="error-banner" role="alert">
          <p>{error}</p>
          <button type="button" onClick={() => void load()}>
            Retry grants
          </button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">Loading support grants...</p>}
      {!loading && !error && requests.length === 0 && <p>No support grant requests.</p>}
      {!loading && requests.length > 0 && (
        <ul>
          {requests.map((row) => (
            <li key={row.requestId}>
              <button
                type="button"
                onClick={() => {
                  setRequestId(row.requestId);
                  setRevision(row.revision);
                  setForm({
                    targetWorkspaceKey: row.targetWorkspaceKey,
                    resourceType: row.resourceType,
                    resourceId: row.resourceId ?? '',
                    reason: row.reason,
                    expiresAt: form.expiresAt,
                  });
                }}
              >
                {row.status} revision {row.revision}: {row.reason}
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submitRequest();
        }}
      >
        <label>
          Target workspace
          <input
            aria-label="Target workspace"
            value={form.targetWorkspaceKey}
            disabled={disabled}
            onChange={(event) => setForm({ ...form, targetWorkspaceKey: event.target.value })}
            required
          />
        </label>
        <label>
          Resource type
          <select
            aria-label="Resource type"
            value={form.resourceType}
            disabled={disabled}
            onChange={(event) =>
              setForm({
                ...form,
                resourceType: event.target.value as GrantResourceType,
              })
            }
          >
            <option value="conversation">conversation</option>
            <option value="plan">plan</option>
            <option value="all_records">all_records</option>
          </select>
        </label>
        <label>
          Resource id
          <input
            aria-label="Resource id"
            value={form.resourceId}
            disabled={disabled}
            onChange={(event) => setForm({ ...form, resourceId: event.target.value })}
          />
        </label>
        <label>
          Request reason
          <input
            aria-label="Request reason"
            value={form.reason}
            disabled={disabled}
            minLength={5}
            onChange={(event) => setForm({ ...form, reason: event.target.value })}
            required
          />
        </label>
        <label>
          Grant expiry
          <input
            aria-label="Grant expiry"
            type="datetime-local"
            value={form.expiresAt}
            disabled={disabled}
            onChange={(event) => setForm({ ...form, expiresAt: event.target.value })}
            required
          />
        </label>
        <button type="submit" disabled={disabled}>
          {requestId ? 'Update request' : 'Request access'}
        </button>
        <button type="button" disabled={disabled || !requestId || revision == null} onClick={() => void approve()}>
          Approve request
        </button>
      </form>
      {!loading && activeGrants.length === 0 && <p>No active support grants.</p>}
      {activeGrants.map((grant) => (
        <p key={grant.grantId}>
          Active grant for {grant.targetWorkspaceKey}
          <button type="button" disabled={disabled} onClick={() => void revoke(grant.grantId)}>
            Revoke grant
          </button>
        </p>
      ))}
    </section>
  );
}
