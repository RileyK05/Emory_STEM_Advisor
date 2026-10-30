import { useState } from 'react';
import { getClient } from '../api/client';
import { errorMessage } from '../lib/errors';
import type { SourceDoc } from '../api/types';

/** Upload one admin source and report the outcome as a note or an error. Never throws. */
export function useSourceUpload(refreshSources: () => Promise<SourceDoc[]>) {
  const [uploading, setUploading] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = async (file: File) => {
    setError(null);
    setNote(null);
    setUploading(true);
    try {
      const source = await getClient().uploadSource(file);
      if (source.status === 'failed') {
        setError(
          source.errorMessage ||
            `“${source.name}” could not be indexed. Check that the file is readable and try another copy.`,
        );
      } else {
        setNote(`“${source.name}” is ${source.status === 'indexed' ? 'ready to use' : source.status}.`);
      }
      if (source.warnings?.length) setNote(source.warnings.join(' '));
      await refreshSources();
    } catch (cause) {
      setError(errorMessage(cause));
      try {
        await refreshSources();
      } catch {
        /* keep the upload error visible */
      }
    } finally {
      setUploading(false);
    }
  };

  return { uploading, note, error, upload };
}
