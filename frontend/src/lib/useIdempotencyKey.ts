import { useMemo, useRef } from 'react';

/** A retried request with an unchanged payload reuses its key so the server can dedupe it;
 *  any change to the payload mints a fresh key. Call `reset` once the request succeeds. */
export function useIdempotencyKey() {
  const current = useRef<{ fingerprint: string; key: string } | null>(null);
  return useMemo(
    () => ({
      keyFor(payload: unknown): string {
        const fingerprint = JSON.stringify(payload);
        if (current.current?.fingerprint !== fingerprint) current.current = { fingerprint, key: crypto.randomUUID() };
        return current.current.key;
      },
      reset() {
        current.current = null;
      },
    }),
    [],
  );
}
