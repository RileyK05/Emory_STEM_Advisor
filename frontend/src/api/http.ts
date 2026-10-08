import type {
  AdvisorClient,
  QueryRequest,
  QueryResponse,
  SourceDoc,
} from './types';

/** Base URL of the backend. Defaults to the Vite dev proxy path. */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    });
  } catch {
    throw new Error(
      'Could not reach the backend API. Is it running, and is VITE_API_MODE=http?',
    );
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`API ${response.status}: ${detail || response.statusText}`);
  }
  return (await response.json()) as T;
}

export class HttpClient implements AdvisorClient {
  async query(req: QueryRequest): Promise<QueryResponse> {
    return request<QueryResponse>('/api/query', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async listSources(): Promise<SourceDoc[]> {
    return request<SourceDoc[]>('/api/sources');
  }

  async uploadSource(file: File): Promise<SourceDoc> {
    const form = new FormData();
    form.append('file', file);
    let response: Response;
    try {
      response = await fetch(`${BASE_URL}/api/sources`, { method: 'POST', body: form });
    } catch {
      throw new Error('Could not reach the backend API to upload the file.');
    }
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`Upload failed (${response.status}): ${detail || response.statusText}`);
    }
    return (await response.json()) as SourceDoc;
  }
}
