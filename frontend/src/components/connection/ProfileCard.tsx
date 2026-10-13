import type { ConnectionDiagnostic, ConnectionProfile } from '../../api/types';

interface Props {
  profile: ConnectionProfile;
  busy: boolean;
  diagnostic: ConnectionDiagnostic | undefined;
  /** Whether the replace-credential form is open for this profile. */
  replacingSecret: boolean;
  /** Whether another profile's replace-credential form is open. */
  replaceBlocked: boolean;
  secretDraft: string;
  onSecretDraftChange: (value: string) => void;
  onEdit: () => void;
  onStartReplace: () => void;
  onCancelReplace: () => void;
  onReplace: () => void;
  onClearSecret: () => void;
  onDiagnose: () => void;
  onDelete: () => void;
}

export function ProfileCard(props: Props) {
  const { profile, busy, diagnostic, replacingSecret, secretDraft } = props;
  return (
    <article className="connection-profile">
      <h3>{profile.name}</h3>
      <p>
        {profile.provider} · {profile.model_id} · revision {profile.revision}
      </p>
      <p>
        Roles: {profile.roles.join(', ')} · scopes: {profile.data_scopes.join(', ') || 'none'} · priority{' '}
        {profile.priority}
      </p>
      <p>
        Credential: {profile.secret_configured ? 'configured (masked)' : 'not configured'} · current evaluation:{' '}
        {profile.eval_passed ? 'passed' : 'not passed'}
      </p>
      <div className="artifact-actions">
        <button type="button" disabled={busy} onClick={props.onEdit}>
          Edit profile
        </button>
        <button type="button" disabled={busy || props.replaceBlocked} onClick={props.onStartReplace}>
          Replace credential
        </button>
        {profile.secret_configured && (
          <button type="button" disabled={busy} onClick={props.onClearSecret}>
            Delete credential
          </button>
        )}
        <button type="button" disabled={busy} onClick={props.onDiagnose}>
          Diagnose
        </button>
        <button type="button" disabled={busy} onClick={props.onDelete}>
          Delete profile
        </button>
      </div>
      {diagnostic && (
        <p role="status">
          Credential diagnostic: {diagnostic.status} · {diagnostic.message}
        </p>
      )}
      {replacingSecret && (
        <div>
          <label>
            Replace {profile.provider === 'cloudflare-clef' ? 'API token' : 'API key'} for “{profile.name}”
            <input
              type="password"
              autoComplete="new-password"
              value={secretDraft}
              onChange={(event) => props.onSecretDraftChange(event.target.value)}
            />
          </label>
          <button type="button" disabled={busy || !secretDraft} onClick={props.onReplace}>
            Replace credential
          </button>
          <button type="button" onClick={props.onCancelReplace}>
            Cancel
          </button>
        </div>
      )}
    </article>
  );
}
