import type {
  ConnectionCapability,
  ConnectionDataScope,
  ConnectionProfile,
  ConnectionProfileWrite,
  ConnectionProvider,
  ConnectionRole,
} from '../../api/types';

export const PROVIDERS: ConnectionProvider[] = [
  'openai',
  'anthropic',
  'google',
  'openai-compatible',
  'hf-local',
  'openai-decisions',
  'cloudflare-clef',
];
export const ROLES: ConnectionRole[] = ['standard', 'complex', 'fast_generation', 'decision'];
export const CAPABILITIES: ConnectionCapability[] = [
  'structured_output',
  'tools',
  'streaming',
  'vision',
  'batch',
  'decision',
];
export const SCOPES: ConnectionDataScope[] = ['institutional_corpus', 'user_supplied_data', 'private_workspace'];

/** The editable profile fields plus the one-time credential, which is never read back from the API. */
export type ProfileFormState = ConnectionProfileWrite & { secret: string };

/** Providers that may only serve the decision role (backend `DECISION_PROVIDERS`). */
export const DECISION_PROVIDERS: ConnectionProvider[] = ['openai-decisions', 'cloudflare-clef'];

export function blankForm(): ProfileFormState {
  return {
    name: '',
    provider: 'openai',
    model_id: '',
    roles: ['standard'],
    capabilities: [],
    data_scopes: ['institutional_corpus'],
    base_url: null,
    account_id: null,
    provider_options: {},
    enabled: true,
    priority: 100,
    secret: '',
  };
}

export function formFromProfile(profile: ConnectionProfile): ProfileFormState {
  return {
    name: profile.name,
    provider: profile.provider,
    model_id: profile.model_id,
    roles: [...profile.roles],
    capabilities: [...profile.capabilities],
    data_scopes: [...profile.data_scopes],
    base_url: profile.base_url,
    account_id: profile.account_id,
    provider_options: profile.provider_options,
    enabled: profile.enabled,
    priority: profile.priority,
    secret: '',
  };
}

/**
 * Normalize provider-dependent state when the provider changes: drop fields that
 * are invalid for the new provider and align the role set with decision-only
 * providers, so the backend does not reject a hidden leftover.
 */
export function providerChange(form: ProfileFormState, provider: ConnectionProvider): ProfileFormState {
  const decision = DECISION_PROVIDERS.includes(provider);
  const roles = decision ? (['decision'] as ConnectionRole[]) : form.roles.filter((role) => role !== 'decision');
  return {
    ...form,
    provider,
    secret: '',
    base_url: provider === 'openai-compatible' ? form.base_url : null,
    account_id: provider === 'cloudflare-clef' ? form.account_id : null,
    roles: roles.length ? roles : (['standard'] as ConnectionRole[]),
  };
}

/** The credential body for a provider: Cloudflare takes a token, everything else an API key. */
export function secretBody(provider: ConnectionProvider, value: string) {
  return { [provider === 'cloudflare-clef' ? 'api_token' : 'api_key']: value };
}

export function toggle<T extends string>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}
