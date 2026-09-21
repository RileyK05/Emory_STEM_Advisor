import type { AdvisorClient, QueryRequest, QueryResponse, SourceDoc } from './types';

export class HttpClient implements AdvisorClient {
  async query(_req: QueryRequest): Promise<QueryResponse> {
    throw new Error('HttpClient not implemented: backend is not available yet. Use VITE_API_MODE=mock.');
  }

  async listSources(): Promise<SourceDoc[]> {
    throw new Error('HttpClient not implemented: backend is not available yet. Use VITE_API_MODE=mock.');
  }

  async uploadSource(_file: File): Promise<SourceDoc> {
    throw new Error('HttpClient not implemented: backend is not available yet. Use VITE_API_MODE=mock.');
  }
}
