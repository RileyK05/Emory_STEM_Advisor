import { useEffect, useState } from 'react';
import { getClient } from '../../api/client';
import { errorMessage, statusOf } from '../../lib/errors';
import type { ConnectionDiagnostic, ConnectionProfile } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { ProfileCard } from './ProfileCard';
import { ProfileForm } from './ProfileForm';
import { blankForm, formFromProfile, secretBody } from './formState';

/** Connection management is offered only when the page itself is served from this machine. */
function isLoopbackHost() {
  const hostname = window.location.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  return hostname === 'localhost' || hostname === '::1' || /^127(?:\.\d{1,3}){3}$/.test(hostname);
}

/**
 * Whether local connection management is offered: only when the page is served from this machine
 * and the backend exposes the admin endpoint (it answers 404 otherwise).
 */
export function useLocalConnectionsAvailable() {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    if (!isLoopbackHost()) return;
    let active = true;
    getClient()
      .listConnectionProfiles()
      .then(() => active && setAvailable(true))
      .catch((cause: unknown) => {
        if (active && statusOf(cause) !== 404) setAvailable(true);
      });
    return () => {
      active = false;
    };
  }, []);
  return available;
}

export function ConnectionSettings() {
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [form, setForm] = useState(blankForm);
  const [editing, setEditing] = useState<string | null>(null);
  const [secretProfile, setSecretProfile] = useState<string | null>(null);
  const [secretDraft, setSecretDraft] = useState('');
  const [diagnostics, setDiagnostics] = useState<Record<string, ConnectionDiagnostic>>({});
  const {
    busy,
    error,
    notice,
    setError,
    setNotice,
    run: perform,
  } = useAsyncAction('Connection settings request failed.');

  useEffect(() => {
    let active = true;
    void getClient()
      .listConnectionProfiles()
      .then((items) => active && setProfiles(items))
      .catch((cause: unknown) => active && setError(errorMessage(cause, 'Could not load local connection settings.')));
    return () => {
      active = false;
    };
  }, []);

  const refresh = async () => {
    setProfiles(await getClient().listConnectionProfiles());
  };
  const closeSecretForm = () => {
    setSecretDraft('');
    setSecretProfile(null);
  };
  const clearForm = () => {
    setEditing(null);
    setForm(blankForm());
  };
  const submit = () =>
    void perform(async () => {
      const { secret, ...write } = form;
      const profile = editing ? profiles.find((item) => item.profile_id === editing) : undefined;
      if (editing && !profile) {
        // The target was deleted while the form was open; never fall through to
        // creating a duplicate.
        clearForm();
        setError('That connection profile no longer exists.');
        return;
      }
      if (editing && profile)
        await getClient().updateConnectionProfile(editing, { ...write, expected_revision: profile.revision });
      else
        await getClient().createConnectionProfile({
          ...write,
          ...(secret && form.provider !== 'hf-local' ? { secret: secretBody(form.provider, secret) } : {}),
        });
      clearForm();
      await refresh();
      setNotice(
        editing
          ? 'Profile saved. Routing remains evaluation-gated.'
          : 'Profile created. Routing remains evaluation-gated.',
      );
    });
  const diagnose = (id: string) =>
    void perform(async () => {
      const result = await getClient().diagnoseConnection(id);
      setDiagnostics((current) => ({ ...current, [id]: result }));
    });
  const replaceSecret = (profile: ConnectionProfile) =>
    void perform(async () => {
      await getClient().replaceConnectionSecret(profile.profile_id, {
        expected_revision: profile.revision,
        secret: secretBody(profile.provider, secretDraft),
      });
      closeSecretForm();
      await refresh();
      setNotice('Credential replaced. Its value is never returned by the API.');
    });
  const clearSecret = (profile: ConnectionProfile) =>
    void perform(async () => {
      await getClient().clearConnectionSecret(profile.profile_id, { expected_revision: profile.revision });
      await refresh();
      setNotice('Credential removed.');
    });
  const removeProfile = (profile: ConnectionProfile) => {
    if (!window.confirm(`Delete connection profile “${profile.name}”?`)) return;
    void perform(async () => {
      await getClient().deleteConnectionProfile(profile.profile_id, { expected_revision: profile.revision });
      if (editing === profile.profile_id) clearForm();
      if (secretProfile === profile.profile_id) closeSecretForm();
      await refresh();
      setNotice('Connection profile deleted.');
    });
  };

  return (
    <section aria-label="Local connection settings" className="connection-settings-panel stack">
      <p className="muted">
        Available only from the local backend. Credentials are sent once to the encrypted server store and never shown
        again. A profile is used for routing only after the current evaluation suite passes.
      </p>
      {profiles.map((profile) => (
        <ProfileCard
          key={profile.profile_id}
          profile={profile}
          busy={busy}
          diagnostic={diagnostics[profile.profile_id]}
          replacingSecret={secretProfile === profile.profile_id}
          replaceBlocked={secretProfile !== null && secretProfile !== profile.profile_id}
          secretDraft={secretDraft}
          onSecretDraftChange={setSecretDraft}
          onEdit={() => {
            setEditing(profile.profile_id);
            setForm(formFromProfile(profile));
          }}
          onStartReplace={() => {
            // Never carry a draft typed for another profile into this target.
            setSecretDraft('');
            setSecretProfile(profile.profile_id);
          }}
          onCancelReplace={closeSecretForm}
          onReplace={() => replaceSecret(profile)}
          onClearSecret={() => clearSecret(profile)}
          onDiagnose={() => diagnose(profile.profile_id)}
          onDelete={() => removeProfile(profile)}
        />
      ))}
      <ProfileForm
        form={form}
        onChange={setForm}
        editing={editing !== null}
        busy={busy}
        onSubmit={submit}
        onClear={clearForm}
      />
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
