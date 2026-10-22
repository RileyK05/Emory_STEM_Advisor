import { useState } from 'react';
import { cancelCatalogRefresh, startCatalogRefresh } from '../api/backoffice';
import type { CatalogRefreshBatchResponse } from '../api/types';
import { errorMessage } from '../lib/errors';

export function CatalogRefreshPanel() {
  const [collectionId, setCollectionId] = useState('');
  const [editionKey, setEditionKey] = useState('');
  const [batch, setBatch] = useState<CatalogRefreshBatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await startCatalogRefresh({ collectionId: collectionId.trim(), editionKey: editionKey.trim() });
      setBatch(row);
      setNotice('Catalog refresh staged.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!batch) return;
    setBusy(true);
    setError(null);
    try {
      const row = await cancelCatalogRefresh(batch.batchId);
      setBatch(row);
      setNotice('Catalog refresh cancelled.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack">
      {!batch && <p>No catalog refresh is in progress.</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void start()}>
            Retry catalog refresh
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      <label>
        Collection id
        <input value={collectionId} onChange={(event) => setCollectionId(event.target.value)} />
      </label>
      <label>
        Edition key
        <input value={editionKey} onChange={(event) => setEditionKey(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !collectionId.trim() || !editionKey.trim()} onClick={() => void start()}>
        Stage catalog refresh
      </button>
      {batch && (
        <>
          <p>Refresh status {batch.status}</p>
          <p>
            Published edition {batch.currentEdition.editionKey} revision {batch.currentEdition.revision}
            {batch.currentEdition.isCurrent ? ' remains current' : ''}
          </p>
          {batch.currentEdition.topicBuildId && <p>Topic build {batch.currentEdition.topicBuildId} stays usable.</p>}
          {batch.status === 'staging' && (
            <button type="button" disabled={busy} onClick={() => void cancel()}>
              Cancel catalog refresh
            </button>
          )}
        </>
      )}
    </section>
  );
}
