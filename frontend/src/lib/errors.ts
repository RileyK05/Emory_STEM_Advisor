/** Shared error rendering for UI failure states. */

export function errorMessage(cause: unknown, fallback?: string): string {
  if (cause instanceof Error) return cause.message;
  return fallback ?? String(cause);
}

export function statusOf(cause: unknown): number | undefined {
  if (typeof cause === 'object' && cause !== null && 'status' in cause) {
    const value = (cause as { status?: unknown }).status;
    return typeof value === 'number' ? value : undefined;
  }
  return undefined;
}
