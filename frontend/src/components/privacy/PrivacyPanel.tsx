import { useEffect, useState } from 'react';
import { getClient } from '../../api/client';
import type { PrivacyExportDownloadResponse, PrivacyExportResponse } from '../../api/types';
import { errorMessage } from '../../lib/errors';

export function PrivacyPanel() {
  const [exports, setExports] = useState<PrivacyExportResponse[] | null>(null);
  const [created, setCreated] = useState<PrivacyExportResponse | null>(null);
  const [download, setDownload] = useState<PrivacyExportDownloadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastAction, setLastAction] = useState<'load' | 'create' | 'download'>('load');

  const load = async () => {
    setLastAction('load');
    setBusy(true);
    setError(null);
    setExports(null);
    try {
      setExports(await getClient().listPrivacyExports());
    } catch (cause) {
      setError(errorMessage(cause));
      setExports([]);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const create = async () => {
    setLastAction('create');
    setBusy(true);
    setError(null);
    try {
      const row = await getClient().createPrivacyExport();
      setCreated(row);
      setExports((current) => [row, ...(current ?? []).filter((item) => item.exportId !== row.exportId)]);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const saveDownload = async () => {
    if (!created?.downloadToken) return;
    setLastAction('download');
    setBusy(true);
    setError(null);
    try {
      setDownload(await getClient().downloadPrivacyExport(created.exportId, created.downloadToken));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const retry = () => {
    if (lastAction === 'create') void create();
    else if (lastAction === 'download') void saveDownload();
    else void load();
  };

  return (
    <section className="stack">
      {exports === null && <p>Loading privacy exports...</p>}
      {exports !== null && exports.length === 0 && created === null && <p>No privacy export is saved.</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={retry}>
            Retry privacy export
          </button>
        </p>
      )}
      <button type="button" disabled={busy} onClick={() => void create()}>
        Create privacy export
      </button>
      {created && <p>Export ready. Download expires {created.expiresAt}.</p>}
      {created?.downloadToken && (
        <button type="button" disabled={busy} onClick={() => void saveDownload()}>
          Download privacy export
        </button>
      )}
      {download && <p>Export digest {download.contentDigest}.</p>}
    </section>
  );
}
