import { useCallback, useEffect, useRef, useState } from 'react';
import type { AdminUser, CohortMember, CohortSummary, FeatureFlagOverride, FeatureFlagSummary } from '../api/types';
import {
  assignAdminCohort,
  deleteAdminFlagOverride,
  listAdminCohortMembers,
  listAdminCohorts,
  listAdminFlagOverrides,
  listAdminFlags,
  listAdminUsers,
  removeAdminCohort,
  setAdminFlagKill,
  setAdminFlagOverride,
} from '../api/backoffice';
import { errorMessage } from '../lib/errors';

export function FeatureFlagsPanel() {
  const [flags, setFlags] = useState<FeatureFlagSummary[]>([]);
  const [selected, setSelected] = useState('');
  const [overrides, setOverrides] = useState<FeatureFlagOverride[]>([]);
  const [scopeType, setScopeType] = useState<'global' | 'cohort' | 'workspace'>('global');
  const [scopeId, setScopeId] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [reason, setReason] = useState('');
  const [killReason, setKillReason] = useState('');
  const [cohorts, setCohorts] = useState<CohortSummary[]>([]);
  const [cohort, setCohort] = useState('');
  const [members, setMembers] = useState<CohortMember[]>([]);
  const [offset, setOffset] = useState(0);
  const [userQuery, setUserQuery] = useState('');
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [userId, setUserId] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const [flagRows, cohortRows, overrideRows, memberRows] = await Promise.all([
        listAdminFlags(),
        listAdminCohorts(),
        selected ? listAdminFlagOverrides(selected) : Promise.resolve([]),
        cohort.trim() ? listAdminCohortMembers(cohort.trim(), offset) : Promise.resolve([]),
      ]);
      if (id !== requestId.current) return;
      setFlags(flagRows);
      setCohorts(cohortRows);
      setOverrides(overrideRows);
      setMembers(memberRows);
      setError(null);
    } catch (cause) {
      if (id === requestId.current) setError(errorMessage(cause));
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [selected, cohort, offset]);

  useEffect(() => {
    void load();
    return () => {
      ++requestId.current;
    };
  }, [load]);

  const mutate = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
      setNotice(message);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };
  const flag = flags.find((row) => row.flagName === selected);
  const target = scopeType === 'global' ? '*' : scopeId.trim();
  const existing = overrides.find((row) => row.scopeType === scopeType && row.scopeId === target);
  const disabled = busy || loading;

  return (
    <section aria-label="Feature rollout controls">
      <h2>Feature rollouts</h2>
      <p>
        Kill switches take precedence. Process settings remain the upper limit; an override cannot enable a feature
        disabled by deployment configuration.
      </p>
      <p>
        Workspace overrides take precedence over cohorts. If several cohorts disagree, a cohort denial wins over cohort
        approvals. Global overrides apply after cohorts.
      </p>
      <button type="button" className="secondary-button" disabled={disabled} onClick={() => void load()}>
        Refresh policy
      </button>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">Loading rollout policy...</p>}
      <label>
        Feature
        <select
          aria-label="Feature to configure"
          value={selected}
          disabled={disabled}
          onChange={(event) => {
            setSelected(event.target.value);
            setOverrides([]);
            setReason('');
            setKillReason('');
            setScopeId('');
            setScopeType('global');
            setNotice(null);
          }}
        >
          <option value="">Select a feature</option>
          {flags.map((row) => (
            <option key={row.flagName} value={row.flagName}>
              {row.flagName}
              {row.killSwitchActive ? ' (stopped)' : ''}
            </option>
          ))}
        </select>
      </label>
      {flag && (
        <>
          <p>{flag.description}</p>
          <p>
            Default: {flag.defaultEnabled ? 'enabled' : 'disabled'}. Deployment ceiling: {flag.envCeilingKey || 'none'}.
            Kill switch: {flag.killSwitchActive ? 'active' : 'inactive'}.
          </p>
          {flag.killReason && <p>Last kill-switch reason: {flag.killReason}</p>}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(
                () =>
                  setAdminFlagKill(selected, {
                    killSwitchActive: !flag.killSwitchActive,
                    reason: killReason.trim(),
                    expectedUpdatedAt: flag.updatedAt,
                  }),
                'Kill switch updated.',
              );
            }}
          >
            <label>
              Kill-switch reason{' '}
              <input
                required
                maxLength={500}
                value={killReason}
                onChange={(event) => setKillReason(event.target.value)}
              />
            </label>
            <button className="secondary-button" disabled={disabled || !killReason.trim()}>
              {flag.killSwitchActive ? 'Release kill switch' : 'Activate kill switch'}
            </button>
          </form>
          <h3>Overrides</h3>
          {overrides.length === 0 ? (
            <p>No overrides for this feature.</p>
          ) : (
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Scope</th>
                    <th>Target</th>
                    <th>Decision</th>
                    <th>Reason</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {overrides.map((row) => (
                    <tr key={row.overrideId}>
                      <td>{row.scopeType}</td>
                      <td>{row.scopeId}</td>
                      <td>{row.enabled ? 'Enabled' : 'Disabled'}</td>
                      <td>{row.reason}</td>
                      <td>
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={disabled}
                          onClick={() => {
                            setScopeType(row.scopeType);
                            setScopeId(row.scopeId);
                            setEnabled(row.enabled);
                            setReason(row.reason || '');
                          }}
                        >
                          Edit {row.scopeType} {row.scopeId}
                        </button>{' '}
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={disabled}
                          onClick={() => void mutate(() => deleteAdminFlagOverride(row), 'Override removed.')}
                        >
                          Remove {row.scopeType} {row.scopeId}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(
                () =>
                  setAdminFlagOverride(selected, {
                    scopeType,
                    scopeId: target,
                    enabled,
                    reason: reason.trim(),
                    expectedUpdatedAt: existing?.updatedAt,
                    requireAbsent: !existing,
                  }),
                'Override saved.',
              );
            }}
          >
            <label>
              Override scope{' '}
              <select
                aria-label="Override scope"
                value={scopeType}
                onChange={(event) => setScopeType(event.target.value as typeof scopeType)}
              >
                <option value="global">Global</option>
                <option value="cohort">Cohort</option>
                <option value="workspace">Workspace</option>
              </select>
            </label>
            {scopeType !== 'global' && (
              <label>
                {scopeType === 'cohort' ? 'Cohort name' : 'Workspace key'}{' '}
                <input required maxLength={100} value={scopeId} onChange={(event) => setScopeId(event.target.value)} />
              </label>
            )}
            <label>
              Override decision{' '}
              <select
                aria-label="Override decision"
                value={String(enabled)}
                onChange={(event) => setEnabled(event.target.value === 'true')}
              >
                <option value="false">Disabled</option>
                <option value="true">Enabled</option>
              </select>
            </label>
            <label>
              Override reason{' '}
              <input required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
            </label>
            <button className="primary-button" disabled={disabled || !target || !reason.trim()}>
              Save override
            </button>
          </form>
        </>
      )}
      <h3>Cohort membership</h3>
      <p>Select a cohort or enter a new name, then search for the person to assign.</p>
      <label>
        Membership cohort{' '}
        <input
          list="rollout-cohorts"
          maxLength={100}
          value={cohort}
          disabled={busy}
          onChange={(event) => {
            setCohort(event.target.value);
            setOffset(0);
            setMembers([]);
          }}
        />
      </label>
      <datalist id="rollout-cohorts">
        {cohorts.map((row) => (
          <option key={row.cohortName} value={row.cohortName}>
            {row.memberCount} members
          </option>
        ))}
      </datalist>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          void listAdminUsers({ query: userQuery.trim(), limit: 50 })
            .then((rows) => {
              setUsers(rows);
              setUserId('');
            })
            .catch((cause) => setError(errorMessage(cause)))
            .finally(() => setBusy(false));
        }}
      >
        <label>
          Find user by name or email <input value={userQuery} onChange={(event) => setUserQuery(event.target.value)} />
        </label>
        <button className="secondary-button" disabled={disabled}>
          Search users
        </button>
      </form>
      {users.length > 0 && (
        <label>
          User to assign{' '}
          <select aria-label="User to assign" value={userId} onChange={(event) => setUserId(event.target.value)}>
            <option value="">Select a person</option>
            {users.map((user) => (
              <option key={user.userId} value={user.userId}>
                {user.displayName || user.email || user.subject} ({user.email || user.workspaceKey})
              </option>
            ))}
          </select>
        </label>
      )}
      <button
        type="button"
        className="primary-button"
        disabled={disabled || !cohort.trim() || !userId}
        onClick={() => void mutate(() => assignAdminCohort(cohort.trim(), userId), 'Cohort member assigned.')}
      >
        Assign to cohort
      </button>
      {cohort.trim() && (
        <>
          {members.length === 0 ? (
            <p>No members on this page.</p>
          ) : (
            <ul>
              {members.map((member) => (
                <li key={member.userId}>
                  {member.displayName || member.email || member.userId} ({member.email || member.role}){' '}
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={disabled}
                    onClick={() =>
                      void mutate(() => removeAdminCohort(cohort.trim(), member.userId), 'Cohort member removed.')
                    }
                  >
                    Remove {member.displayName || member.email || member.userId}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="secondary-button"
            disabled={disabled || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous members
          </button>{' '}
          <button
            type="button"
            className="secondary-button"
            disabled={disabled || members.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            Next members
          </button>
        </>
      )}
    </section>
  );
}
