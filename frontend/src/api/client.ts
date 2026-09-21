import type { AdvisorClient } from './types';
import { MockClient } from './mock';
import { HttpClient } from './http';

let cached: AdvisorClient | null = null;

/** Single entry point for the rest of the app. Swap VITE_API_MODE=http to
 *  move from the mock client to the real backend client. */
export function getClient(): AdvisorClient {
  if (cached) return cached;
  const mode = import.meta.env.VITE_API_MODE ?? 'mock';
  cached = mode === 'http' ? new HttpClient() : new MockClient();
  return cached;
}
