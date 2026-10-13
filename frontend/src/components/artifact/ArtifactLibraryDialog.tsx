import { useEffect, useState } from 'react';
import { getClient } from '../../api/client';
import type { NativeArtifactSummary } from '../../api/types';
import { errorMessage } from '../../lib/errors';
import { Dialog } from '../shell/Dialog';
import { KIND_LABEL, KindIcon, RefreshIcon } from '../icons';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Open the artifact in the canvas (and keep it with the current chat). */
  onOpen: (artifactId: string) => Promise<void>;
}

function LibraryList({ onClose, onOpen }: Omit<Props, 'open'>) {
  const [items, setItems] = useState<NativeArtifactSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setError(null);
    try {
      setItems(await getClient().listArtifacts());
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };
  useEffect(() => {
    void refresh();
  }, []);

  const open = async (artifactId: string) => {
    setBusy(true);
    setError(null);
    try {
      await onOpen(artifactId);
      onClose();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="row-between">
        <span className="muted">{items ? `${items.length} saved` : 'Loading…'}</span>
        <button type="button" className="ghost-button" onClick={() => void refresh()} disabled={busy}>
          <RefreshIcon size={16} /> Refresh
        </button>
      </div>
      <ul className="artifact-list">
        {items?.map((item) => (
          <li key={item.artifactId}>
            <button type="button" className="artifact-card" disabled={busy} onClick={() => void open(item.artifactId)}>
              <span className="artifact-card-icon">
                <KindIcon kind={item.kind} />
              </span>
              <span className="artifact-card-text">
                <strong>{item.title}</strong>
                <span className="muted">
                  {KIND_LABEL[item.kind]}
                  {item.status === 'needs_recalculation' ? ' · needs recalculation' : ''}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {items?.length === 0 && !error && <p className="muted">No saved artifacts yet.</p>}
      {error && (
        <p className="form-error" role="alert">
          Couldn’t load your artifacts: {error}
        </p>
      )}
    </div>
  );
}

export function ArtifactLibraryDialog({ open, onClose, onOpen }: Props) {
  return (
    <Dialog
      open={open}
      title="All artifacts"
      description="Everything you’ve made or imported. Opening one adds it to this chat."
      onClose={onClose}
    >
      {open && <LibraryList onClose={onClose} onOpen={onOpen} />}
    </Dialog>
  );
}
