import { useCallback, useEffect, useMemo, useState } from 'react';
import type { AuthSession, DebugSession, DebugSetting, DebugSettingsResponse } from '../api/types';
import {
  createDebugSession,
  deleteDebugSession,
  getDebugSession,
  getDebugSettings,
  updateDebugSession,
} from '../api/backoffice';
import { isDemoMode } from '../api/client';
import { ApiError, setDebugSessionToken } from '../api/http';
import { AppProvider } from '../state/AppContext';
import { Shell } from '../components/shell/Shell';
import { TopBar } from '../components/shell/TopBar';
import { SessionGate } from '../app/SessionGate';
import { formatTimestamp } from '../lib/format';
import { useAsyncAction } from '../lib/useAsyncAction';
import { useLoad } from '../lib/useLoad';

const TOKEN_KEY = 'advisor-debug-session';

type Values = Record<string, unknown>;

function initialValues(
  registry: DebugSettingsResponse,
  session: DebugSession,
): {
  settings: Values;
  retrieval: Values;
  prompt: string;
} {
  const settings = Object.fromEntries(registry.settings.map((item) => [item.name, item.value]));
  const retrieval = Object.fromEntries(registry.retrieval.map((item) => [item.name, item.value]));
  return {
    settings: { ...settings, ...(session.overrides.settings ?? {}) },
    retrieval: { ...retrieval, ...(session.overrides.retrieval ?? {}) },
    prompt: session.systemPrompt ?? registry.defaultSystemPrompt,
  };
}

function changed(base: DebugSetting[], values: Values): Values {
  const output: Values = {};
  for (const field of base) {
    const value = values[field.name];
    if (value !== field.value) output[field.name] = value;
  }
  return output;
}

function Field({
  field,
  value,
  onChange,
}: {
  field: DebugSetting;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (field.type === 'bool') {
    return (
      <label className="debug-field debug-bool">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        <span>
          <strong>{field.label}</strong>
          <small>{field.description}</small>
        </span>
      </label>
    );
  }
  if (field.type === 'str' && field.choices?.length) {
    return (
      <label className="debug-field">
        <span>
          <strong>{field.label}</strong>
          <small>{field.description}</small>
        </span>
        <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {field.choices.map((choice) => (
            <option key={choice || '(default)'} value={choice}>
              {choice || '(default)'}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="debug-field">
      <span>
        <strong>{field.label}</strong>
        <small>{field.description}</small>
      </span>
      <input
        type={field.type === 'int' || field.type === 'float' ? 'number' : 'text'}
        step={field.type === 'float' ? '0.01' : undefined}
        value={String(value ?? '')}
        onChange={(e) =>
          onChange(field.type === 'int' || field.type === 'float' ? Number(e.target.value) : e.target.value)
        }
      />
    </label>
  );
}

function Group({
  title,
  fields,
  values,
  onChange,
}: {
  title: string;
  fields: DebugSetting[];
  values: Values;
  onChange: (name: string, value: unknown) => void;
}) {
  const groups = useMemo(() => {
    const output = new Map<string, DebugSetting[]>();
    for (const field of fields) {
      output.set(field.group, [...(output.get(field.group) ?? []), field]);
    }
    return [...output.entries()];
  }, [fields]);
  return (
    <section className="debug-section">
      <h2>{title}</h2>
      {groups.map(([group, groupFields]) => (
        <div className="debug-group" key={group}>
          <h3>{group}</h3>
          {groupFields.map((field) => (
            <Field
              key={field.name}
              field={field}
              value={values[field.name]}
              onChange={(value) => onChange(field.name, value)}
            />
          ))}
        </div>
      ))}
    </section>
  );
}

type DebugBoot = {
  registry: DebugSettingsResponse;
  session: DebugSession;
  token: string;
};

let pendingBoot: Promise<DebugBoot> | null = null;

/** One in-flight boot shared by both StrictMode effect executions. */
function bootDebug(): Promise<DebugBoot> {
  if (!pendingBoot) {
    pendingBoot = (async () => {
      const registry = await getDebugSettings();
      const stored = window.sessionStorage.getItem(TOKEN_KEY);
      let value: DebugSession | null = null;
      let token = stored;
      if (stored) {
        try {
          value = await getDebugSession(stored);
        } catch (cause) {
          if (!(cause instanceof ApiError) || ![401, 403, 404].includes(cause.status)) throw cause;
          window.sessionStorage.removeItem(TOKEN_KEY);
          token = null;
        }
      }
      if (!value) {
        const created = await createDebugSession({ overrides: {}, systemPrompt: null });
        token = created.token;
        window.sessionStorage.setItem(TOKEN_KEY, created.token);
        value = created;
      }
      return { registry, session: value, token: token as string };
    })();
    pendingBoot.catch(() => {
      pendingBoot = null;
    });
  }
  return pendingBoot;
}

function DebugSurface({ session, onLogout }: { session: AuthSession; onLogout?: () => void }) {
  const [tab, setTab] = useState<'settings' | 'chat'>('settings');
  const [registry, setRegistry] = useState<DebugSettingsResponse | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [info, setInfo] = useState<DebugSession | null>(null);
  const [settingsValues, setSettingsValues] = useState<Values>({});
  const [retrievalValues, setRetrievalValues] = useState<Values>({});
  const [prompt, setPrompt] = useState('');
  const action = useAsyncAction();
  const { error, run } = action;

  const applySession = useCallback((next: DebugSettingsResponse, value: DebugSession) => {
    const start = initialValues(next, value);
    setRegistry(next);
    setInfo(value);
    setSettingsValues(start.settings);
    setRetrievalValues(start.retrieval);
    setPrompt(start.prompt);
  }, []);

  const { loading } = useLoad(bootDebug, [], {
    onLoad: (boot) => {
      setDebugSessionToken(boot.token);
      setToken(boot.token);
      applySession(boot.registry, boot.session);
    },
    setError: action.setError,
  });
  const busy = action.busy || loading;

  useEffect(() => () => setDebugSessionToken(null), []);

  if (session.role !== 'admin') {
    return (
      <div className="shell">
        <TopBar session={session} onLogout={onLogout} />
        <main className="session-gate">
          <div className="gate-card">
            <h2>Administrator access required</h2>
            <p>Debug overrides are scoped to institutional administrators.</p>
            <a className="primary-button" href="/">
              Return to the advisor
            </a>
          </div>
        </main>
      </div>
    );
  }

  if (isDemoMode()) {
    return (
      <div className="shell">
        <TopBar session={session} onLogout={onLogout} />
        <main className="session-gate">
          <div className="gate-card">
            <h2>Debug needs the real API</h2>
            <p>Run the debug bundle with `VITE_API_MODE=http`.</p>
          </div>
        </main>
      </div>
    );
  }

  const save = async () => {
    if (!registry || !token) return;
    await run(async () => {
      const updated = await updateDebugSession(token, {
        overrides: {
          settings: changed(registry.settings, settingsValues),
          retrieval: changed(registry.retrieval, retrievalValues),
        },
        systemPrompt: prompt === registry.defaultSystemPrompt ? null : prompt,
      });
      applySession(registry, updated);
    });
  };

  const reset = async () => {
    if (!registry || !token) return;
    await run(async () =>
      applySession(registry, await updateDebugSession(token, { overrides: {}, systemPrompt: null })),
    );
  };

  const renew = () =>
    run(async () => {
      if (token) await deleteDebugSession(token).catch(() => undefined);
      const created = await createDebugSession({ overrides: {}, systemPrompt: null });
      window.sessionStorage.setItem(TOKEN_KEY, created.token);
      setDebugSessionToken(created.token);
      setToken(created.token);
      if (registry) applySession(registry, created);
    });

  if (tab === 'chat') {
    return (
      <div className="debug-chat">
        <div className="debug-banner">
          <strong>Debug chat</strong>
          <span>Overrides are active for this browser session.</span>
          <button type="button" className="secondary-button" onClick={() => setTab('settings')}>
            Back to overrides
          </button>
          <a href="/">Main</a>
        </div>
        <AppProvider key={`${session.mode}:${session.workspaceKey ?? 'development'}:debug`} session={session}>
          <Shell onLogout={onLogout} />
        </AppProvider>
      </div>
    );
  }

  return (
    <div className="shell admin-shell">
      <TopBar session={session} onLogout={onLogout} />
      <div className="surface-nav">
        <strong>Debug Overrides</strong>
        <a href="/">Main</a>
        <a href="/admin.html">Admin</a>
      </div>
      <main className="admin-main debug-main">
        <div className="admin-tabs">
          <button type="button" className="admin-tab active" onClick={() => setTab('settings')}>
            Settings
          </button>
          <button type="button" className="admin-tab" disabled={busy || !token} onClick={() => setTab('chat')}>
            Test chat
          </button>
          <button type="button" className="secondary-button" onClick={() => void save()} disabled={busy}>
            Save overrides
          </button>
          <button type="button" className="secondary-button" onClick={() => void reset()} disabled={busy}>
            Reset
          </button>
          <button type="button" className="secondary-button" onClick={() => void renew()} disabled={busy}>
            New session
          </button>
        </div>
        {error && (
          <p role="alert" className="admin-error">
            {error}
          </p>
        )}
        {busy && !registry && <p role="status">Creating a debug session…</p>}
        {registry && (
          <>
            <section className="debug-section">
              <h2>Answer system prompt</h2>
              <p className="debug-hint">
                This replaces the system message for this session’s legacy advising answers. The deterministic citation
                and scope rules still apply in the code path.
              </p>
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={14}
                className="debug-prompt"
                spellCheck={false}
              />
            </section>
            <Group
              title="Runtime settings"
              fields={registry.settings}
              values={settingsValues}
              onChange={(name, value) => setSettingsValues((current) => ({ ...current, [name]: value }))}
            />
            <Group
              title="Retrieval config"
              fields={registry.retrieval}
              values={retrievalValues}
              onChange={(name, value) => setRetrievalValues((current) => ({ ...current, [name]: value }))}
            />
            {info && <p className="debug-hint">Session expires {formatTimestamp(info.expiresAt)}.</p>}
          </>
        )}
      </main>
    </div>
  );
}

export function DebugApp() {
  return <SessionGate render={(session, logout) => <DebugSurface session={session} onLogout={logout} />} />;
}
