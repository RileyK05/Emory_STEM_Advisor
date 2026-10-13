import { useState } from 'react';
import { isDemoMode } from '../../api/client';
import { useApp } from '../../state/AppContext';
import { errorMessage } from '../../lib/errors';
import { ArtifactBatchManager } from '../batch/ArtifactBatchManager';
import { ConnectionSettings } from '../connection/ConnectionSettings';
import { GapReviewPanel } from '../gap/GapReviewPanel';
import { Dialog } from './Dialog';

type ToolTab = 'gaps' | 'batches' | 'connections' | 'developer';

interface Props {
  open: boolean;
  onClose: () => void;
  connectionsAvailable: boolean;
  onOpenRevision: (artifactId: string, revisionId: string) => Promise<void>;
}

function DeveloperTab() {
  const { showDetails, toggleShowDetails } = useApp();
  return (
    <div className="stack">
      <label className="toggle-row">
        <input type="checkbox" role="switch" checked={showDetails} onChange={toggleShowDetails} />
        <span>
          <strong>Show retrieval details under answers</strong>
          <small>
            Seams, fused passages, the exact prompt, and the model call for each one-off answer. Useful when debugging
            retrieval; not needed for advising.
          </small>
        </span>
      </label>
      <p className="muted small">API mode: {isDemoMode() ? 'mock (demo)' : 'HTTP backend'}</p>
    </div>
  );
}

export function ToolsDialog({ open, onClose, connectionsAvailable, onOpenRevision }: Props) {
  const demo = isDemoMode();
  const tabs: { key: ToolTab; label: string }[] = [
    ...(demo ? [] : [{ key: 'gaps' as const, label: 'Unanswered questions' }]),
    ...(demo ? [] : [{ key: 'batches' as const, label: 'Bulk drafts' }]),
    ...(connectionsAvailable ? [{ key: 'connections' as const, label: 'Model connections' }] : []),
    { key: 'developer', label: 'Developer' },
  ];
  const [tab, setTab] = useState<ToolTab>(tabs[0].key);
  const active = tabs.some((item) => item.key === tab) ? tab : tabs[0].key;
  const [openError, setOpenError] = useState<string | null>(null);

  return (
    <Dialog
      open={open}
      title="Tools"
      description="Review, bulk, and setup tools for advisors and admins."
      onClose={onClose}
      size="wide"
    >
      <div className="tabs" role="tablist" aria-label="Tools">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active === item.key}
            className={active === item.key ? 'tab tab-on' : 'tab'}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="tool-panel">
        {active === 'gaps' && <GapReviewPanel />}
        {active === 'batches' && (
          <ArtifactBatchManager
            onOpenRevision={async (artifactId, revisionId) => {
              setOpenError(null);
              try {
                await onOpenRevision(artifactId, revisionId);
                onClose();
              } catch (cause) {
                setOpenError(errorMessage(cause));
              }
            }}
          />
        )}
        {openError && (
          <p className="form-error" role="alert">
            {openError}
          </p>
        )}
        {active === 'connections' && <ConnectionSettings />}
        {active === 'developer' && <DeveloperTab />}
      </div>
    </Dialog>
  );
}
