import { useState } from 'react';
import { errorMessage } from './errors';

interface RunOptions {
  /** Leave the current notice in place instead of clearing it when the action starts. */
  keepNotice?: boolean;
}

/** Busy/error/notice state around one user-initiated async action at a time. */
export function useAsyncAction(errorFallback?: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const run = async (action: () => Promise<void>, options: RunOptions = {}) => {
    setBusy(true);
    setError(null);
    if (!options.keepNotice) setNotice(null);
    try {
      await action();
    } catch (cause) {
      setError(errorMessage(cause, errorFallback));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, notice, setError, setNotice, run };
}
