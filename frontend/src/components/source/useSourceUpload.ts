import { useState } from 'react';
import { errorMessage } from '../../lib/errors';
import type { SourceDoc } from '../../api/types';

/** Upload one file and report the outcome as a note (success, warnings) or an error. Never throws. */
export function useSourceUpload(
  uploadSource: (file: File) => Promise<SourceDoc>,
  refreshSources: () => Promise<SourceDoc[]>,
) {
  const [uploading, setUploading] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = async (file: File) => {
    setError(null);
    setNote(null);
    setUploading(true);
    try {
      const source = await uploadSource(file);
      if (source.status === 'failed') {
        setError(
          source.errorMessage ||
            `“${source.name}” could not be indexed. Check that the file is readable and try another copy.`,
        );
      } else {
        setNote(`“${source.name}” is ${source.status === 'indexed' ? 'ready to use' : source.status}.`);
      }
      if (source.warnings?.length) setNote(source.warnings.join(' '));
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
