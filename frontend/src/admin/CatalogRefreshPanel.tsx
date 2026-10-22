import { useState } from 'react';
import {
  cancelCatalogRefresh,
  getCatalogRefreshStatus,
  publishCatalogRefresh,
  startCatalogRefresh,
} from '../api/backoffice';
import type { CatalogEditionItem, CatalogRefreshBatchResponse } from '../api/types';
import { useAsyncAction } from '../lib/useAsyncAction';

function editionLabel(edition: CatalogEditionItem) {
  return `${edition.editionKey} (revision ${edition.revision}, ${edition.sourceIds?.length ?? 0} sources)`;
}

/** Stage a catalog edition from the collection's current sources, then publish or cancel it. */
export function CatalogRefreshPanel() {
  const [collectionId, setCollectionId] = useState('');
  const [editionKey, setEditionKey] = useState('');
  const [current, setCurrent] = useState<CatalogEditionItem | null>(null);
  const [batch, setBatch] = useState<CatalogRefreshBatchResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const { busy, error, notice, setNotice, run } = useAsyncAction();
  const collection = collectionId.trim();

  const apply = (row: CatalogRefreshBatchResponse) => {
    setBatch(row);
    setCurrent(row.currentEdition ?? null);
  };

  const load = () =>
    run(async () => {
      const status = await getCatalogRefreshStatus(collection);
      setCurrent(status.currentEdition ?? null);
      setBatch(status.staging ?? null);
      setLoaded(true);
    });

  const start = () =>
    run(async () => {
      apply(await startCatalogRefresh({ collectionId: collection, editionKey: editionKey.trim() }));
      setNotice('Catalog refresh staged.');
    });

  const publish = (staged: CatalogRefreshBatchResponse) =>
    run(async () => {
      apply(await publishCatalogRefresh(staged.batchId, staged.baseRevision));
      setNotice('Catalog edition published.');
    });

  const cancel = (staged: CatalogRefreshBatchResponse) =>
    run(async () => {
      apply(await cancelCatalogRefresh(staged.batchId));
      setNotice('Catalog refresh cancelled.');
    });

  const staging = batch?.status === 'staging' ? batch : null;
  return (
    <section className="stack">
      {error && <p role="alert">{error}</p>}
      {notice && <p>{notice}</p>}
      <label>
        Collection id
        <input
          value={collectionId}
          onChange={(event) => {
            setCollectionId(event.target.value);
            setLoaded(false);
          }}
        />
      </label>
      <button type="button" disabled={busy || !collection} onClick={() => void load()}>
        Load catalog edition
      </button>
      {loaded && (
        <>
          <p>{current ? `Published edition ${editionLabel(current)}` : 'No catalog edition is published yet.'}</p>
          {current?.topicBuildId && (
            <p>Topic build {current.topicBuildId} stays usable until a new edition publishes.</p>
          )}
          {staging ? (
            <>
              <p>Staged edition {editionLabel(staging.stagedEdition)}</p>
              <button type="button" disabled={busy} onClick={() => void publish(staging)}>
                Publish catalog edition
              </button>
              <button type="button" disabled={busy} onClick={() => void cancel(staging)}>
                Cancel catalog refresh
              </button>
            </>
          ) : (
            <>
              <label>
                Edition key
                <input value={editionKey} onChange={(event) => setEditionKey(event.target.value)} />
              </label>
              <button type="button" disabled={busy || !editionKey.trim()} onClick={() => void start()}>
                Stage catalog refresh
              </button>
            </>
          )}
        </>
      )}
    </section>
  );
}
