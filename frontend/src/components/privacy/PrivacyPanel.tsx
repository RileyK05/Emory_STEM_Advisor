import { useEffect, useState } from 'react';
import { getClient } from '../../api/client';
import type { PrivacyExportDownloadResponse, PrivacyExportResponse } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';

export function PrivacyPanel() {
  const [exports, setExports] = useState<PrivacyExportResponse[] | null>(null);
  const [created, setCreated] = useState<PrivacyExportResponse | null>(null);
  const [download, setDownload] = useState<PrivacyExportDownloadResponse | null>(null);
  const { busy, error, run } = useAsyncAction();
  const [lastAction, setLastAction] = useState<'load' | 'create' | 'download'>('load');

  const load = () => {
    setLastAction('load');
    return run(async () => {
      setExports(null);
      try {
        setExports(await getClient().listPrivacyExports());
      } catch (cause) {
        setExports([]);
        throw cause;
      }
    });
  };

  useEffect(() => {
    void load();
  }, []);

  const create = () => {
    setLastAction('create');
    return run(async () => {
      const row = await getClient().createPrivacyExport();
      setCreated(row);
      setExports((current) => [row, ...(current ?? []).filter((item) => item.exportId !== row.exportId)]);
    });
  };

  const saveDownload = async () => {
    if (!created?.downloadToken) return;
    const { exportId, downloadToken } = created;
    setLastAction('download');
    await run(async () => setDownload(await getClient().downloadPrivacyExport(exportId, downloadToken)));
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
