import { ApiError } from './http';
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
  AdminUserUpdate,
  DebugSession,
  DebugSessionCreated,
  DebugSettingsResponse,
} from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

async function request<T>(path: string, init: RequestInit = {}, debugToken?: string | null): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    credentials: 'same-origin',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(debugToken ? { 'X-Debug-Session': debugToken } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text || response.statusText;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === 'string') detail = parsed.detail;
      else if (parsed.detail != null) detail = JSON.stringify(parsed.detail);
    } catch {
      /* plain text */
    }
    throw new ApiError(`${response.status}: ${detail}`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getAdminOverview(): Promise<AdminOverview> {
  return request<AdminOverview>('/api/admin/overview');
}

export function listAdminUsers(
  params: {
    query?: string;
    role?: string;
    status?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<AdminUser[]> {
  const search = new URLSearchParams();
  if (params.query) search.set('query', params.query);
  if (params.role) search.set('role', params.role);
  if (params.status) search.set('status', params.status);
  search.set('limit', String(params.limit ?? 50));
  search.set('offset', String(params.offset ?? 0));
  return request<AdminUser[]>(`/api/admin/users?${search}`);
}

export function updateAdminUser(id: string, body: AdminUserUpdate): Promise<AdminUser> {
  return request<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function revokeAdminUserSessions(id: string): Promise<{ revoked: number }> {
  return request<{ revoked: number }>(`/api/admin/users/${encodeURIComponent(id)}/revoke-sessions`, { method: 'POST' });
}

export function listAdminAudit(limit = 100): Promise<AdminAuditEntry[]> {
  return request<AdminAuditEntry[]>(`/api/admin/audit?limit=${limit}`);
}

export function listAdminConversations(
  params: {
    workspace?: string;
    query?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<AdminConversation[]> {
  const search = new URLSearchParams();
  if (params.workspace) search.set('workspace', params.workspace);
  if (params.query) search.set('query', params.query);
  search.set('limit', String(params.limit ?? 50));
  search.set('offset', String(params.offset ?? 0));
  return request<AdminConversation[]>(`/api/admin/conversations?${search}`);
}

export function getAdminTranscript(id: string): Promise<AdminTranscript> {
  return request<AdminTranscript>(`/api/admin/conversations/${encodeURIComponent(id)}/transcript`);
}

export function getAdminUser(id: string): Promise<AdminUser> {
  return request<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}`);
}

export function listAdminUserSessions(id: string): Promise<AdminUserSession[]> {
  return request<AdminUserSession[]>(`/api/admin/users/${encodeURIComponent(id)}/sessions`);
}

export function deleteAdminConversation(id: string): Promise<void> {
  return request<void>(`/api/admin/conversations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

export function listAdminJobs(status = '', limit = 100, offset = 0): Promise<AdminJob[]> {
  const search = new URLSearchParams({
    status,
    limit: String(limit),
    offset: String(offset),
  });
  return request<AdminJob[]>(`/api/admin/jobs?${search}`);
}

export function cancelAdminJob(id: string): Promise<AdminJob> {
  return request<AdminJob>(`/api/admin/jobs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
  });
}

export function listAdminSources(workspace = '', limit = 100, offset = 0): Promise<AdminSource[]> {
  const search = new URLSearchParams({
    workspace,
    limit: String(limit),
    offset: String(offset),
  });
  return request<AdminSource[]>(`/api/admin/sources?${search}`);
}

export function listAdminArtifacts(limit = 100, offset = 0): Promise<AdminArtifact[]> {
  const search = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<AdminArtifact[]>(`/api/admin/artifacts?${search}`);
}

export function listAdminCollections(limit = 100, offset = 0): Promise<AdminCollection[]> {
  const search = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<AdminCollection[]>(`/api/admin/collections?${search}`);
}

export function createAdminCollection(name: string): Promise<AdminCollection> {
  return request<AdminCollection>('/api/admin/collections', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function renameAdminCollection(id: string, name: string): Promise<AdminCollection> {
  return request<AdminCollection>(`/api/admin/collections/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });
}

export function deleteAdminCollection(id: string): Promise<void> {
  return request<void>(`/api/admin/collections/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

export function getDebugSettings(): Promise<DebugSettingsResponse> {
  return request<DebugSettingsResponse>('/api/debug/settings');
}

export function createDebugSession(body: {
  overrides?: { settings?: Record<string, unknown>; retrieval?: Record<string, unknown> };
  systemPrompt?: string | null;
}): Promise<DebugSessionCreated> {
  return request<DebugSessionCreated>('/api/debug/sessions', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getDebugSession(token: string): Promise<DebugSession> {
  return request<DebugSession>('/api/debug/session', {}, token);
}

export function updateDebugSession(
  token: string,
  body: {
    overrides?: { settings?: Record<string, unknown>; retrieval?: Record<string, unknown> };
    systemPrompt?: string | null;
  },
): Promise<DebugSession> {
  return request<DebugSession>(
    '/api/debug/session',
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    },
    token,
  );
}

export function deleteDebugSession(token: string): Promise<void> {
  return request<void>('/api/debug/session', { method: 'DELETE' }, token);
}
