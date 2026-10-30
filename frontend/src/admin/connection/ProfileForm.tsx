import type { ConnectionProvider } from '../../api/types';
import { CheckboxGroup } from './CheckboxGroup';
import { CAPABILITIES, PROVIDERS, ROLES, SCOPES, providerChange } from './formState';
import type { ProfileFormState } from './formState';

interface Props {
  form: ProfileFormState;
  onChange: (form: ProfileFormState) => void;
  /** Editing an existing profile (credentials are managed on its card), or creating one. */
  editing: boolean;
  busy: boolean;
  onSubmit: () => void;
  onClear: () => void;
}

export function ProfileForm({ form, onChange, editing, busy, onSubmit, onClear }: Props) {
  const set = (patch: Partial<ProfileFormState>) => onChange({ ...form, ...patch });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <h3>{editing ? 'Edit connection profile' : 'Add connection profile'}</h3>
      <label>
        Name
        <input required maxLength={120} value={form.name} onChange={(event) => set({ name: event.target.value })} />
      </label>
      <label>
        Provider
        <select
          value={form.provider}
          onChange={(event) => onChange(providerChange(form, event.target.value as ConnectionProvider))}
        >
          {PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider}
            </option>
          ))}
        </select>
      </label>
      <label>
        Model ID
        <input
          required
          maxLength={200}
          value={form.model_id}
          onChange={(event) => set({ model_id: event.target.value })}
        />
      </label>
      {form.provider === 'openai-compatible' && (
        <label>
          Base URL
          <input
            required
            type="url"
            value={form.base_url ?? ''}
            onChange={(event) => set({ base_url: event.target.value || null })}
          />
        </label>
      )}
      {form.provider === 'cloudflare-clef' && (
        <label>
          Account ID
          <input
            required
            value={form.account_id ?? ''}
            onChange={(event) => set({ account_id: event.target.value || null })}
          />
        </label>
      )}
      {!editing && form.provider !== 'hf-local' && (
        <label>
          Provider credential
          <input
            type="password"
            autoComplete="new-password"
            value={form.secret}
            onChange={(event) => set({ secret: event.target.value })}
          />
        </label>
      )}
      <CheckboxGroup
        legend="Routing roles"
        options={ROLES}
        selected={form.roles}
        onChange={(roles) => set({ roles })}
      />
      <CheckboxGroup
        legend="Capabilities"
        options={CAPABILITIES}
        selected={form.capabilities}
        onChange={(capabilities) => set({ capabilities })}
      />
      <CheckboxGroup
        legend="Data scopes"
        options={SCOPES}
        selected={form.data_scopes}
        onChange={(data_scopes) => set({ data_scopes })}
      />
      <label>
        Priority
        <input
          type="number"
          min={0}
          max={1000000}
          value={form.priority}
          onChange={(event) => set({ priority: Number(event.target.value) })}
        />
      </label>
      <label>
        Enabled profile
        <input type="checkbox" checked={form.enabled} onChange={(event) => set({ enabled: event.target.checked })} />
      </label>
      <p>
        Profile edits cannot set evaluation status or promote a route. Only backend evaluation records can make a
        profile eligible.
      </p>
      <div className="artifact-actions">
        <button type="submit" disabled={busy || !form.roles.length}>
          {editing ? 'Save profile' : 'Create profile'}
        </button>
        <button type="button" onClick={onClear}>
          Clear form
        </button>
      </div>
    </form>
  );
}
