import type { AdvisorClient } from './types';
import { MockClient } from './mock';
import { HttpClient } from './http';

let cached: AdvisorClient | null = null;

/** True when the app runs on the in-browser mock (VITE_API_MODE unset or `mock`):
 *  answers work, but nothing is saved and artifacts are unavailable. */
export function isDemoMode(): boolean {
  return (import.meta.env.VITE_API_MODE ?? 'mock') !== 'http';
}

/** Single entry point for the rest of the app. Swap VITE_API_MODE=http to
 *  move from the mock client to the real backend client. */
export function getClient(): AdvisorClient {
  if (cached) return cached;
  cached = isDemoMode() ? new MockClient() : new HttpClient();
  return cached;
}
