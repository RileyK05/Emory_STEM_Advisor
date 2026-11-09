import { useState } from 'react';
import { CloseIcon, KIND_LABEL, KindIcon } from '../icons';
import { ArtifactEditor } from './ArtifactEditor';
import { readableStage } from './labels';
import type { ArtifactWorkspace, RunItem } from './useArtifactWorkspace';
import { runOutputs } from './useArtifactWorkspace';

function RunCard({
  item,
  onCancel,
  onRetry,
  onDismiss,
}: {
  item: RunItem;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const lastEvent = [...item.events].reverse().find((event) => event.stage);
  const lastStage = lastEvent?.stage;
  const stageText =
    lastStage === 'writing' && lastEvent?.unit
      ? `Writing section ${lastEvent.unit}…`
      : lastStage === 'repairing' && lastEvent?.unit
        ? `Fixing ${lastEvent.unit}…`
        : (readableStage(lastStage) ?? 'Working…');
  const state = item.run.state;
  const active = state === 'queued' || state === 'running';
  const nothingSaved = state === 'succeeded' && runOutputs(item.run, item.events).length === 0;
  return (
    <div className={'artifact-card run-card' + (active ? ' run-active' : ' run-done')} role="status">
      <span className="artifact-card-icon">
        <KindIcon kind={item.kind} />
      </span>
      <div className="artifact-card-text">
        <strong>{item.label || `New ${KIND_LABEL[item.kind].toLowerCase()}`}</strong>
        <span className="muted">
          {active
            ? state === 'queued'
              ? 'Waiting to start…'
              : stageText
            : state === 'failed'
              ? "Couldn't make this. Try again."
              : state === 'cancelled'
                ? 'Cancelled.'
                : nothingSaved
                  ? 'Finished, but nothing was saved.'
                  : 'Finishing…'}
        </span>
        {state !== 'failed' && item.run.error && <span className="form-error">{item.run.error}</span>}
        {item.pollError && <span className="muted">Status check failed, retrying: {item.pollError}</span>}
        {active && <span className="progress" aria-hidden />}
      </div>
      {active ? (
        <button type="button" className="ghost-button" onClick={onCancel}>
          Cancel
        </button>
      ) : (
        <>
          {state === 'failed' && (
            <button type="button" className="ghost-button" onClick={onRetry}>
              Retry
            </button>
          )}
          <button type="button" className="icon-button" aria-label="Dismiss" onClick={onDismiss}>
            <CloseIcon size={16} />
          </button>
        </>
      )}
    </div>
  );
}

/** The one artifact beside the chat: progress while it is made, then its preview. */
export function ArtifactPanel({ workspace }: { workspace: ArtifactWorkspace }) {
  const [openError, setOpenError] = useState<string | null>(null);
  const run = workspace.runs[0] ?? null;

  const close = () => {
    workspace.closeArtifact();
    workspace.setPanelOpen(false);
  };

  return (
    <aside className={'artifact-panel' + (workspace.record ? ' artifact-panel-canvas' : '')} aria-label="Artifact">
      {workspace.record ? (
        <ArtifactEditor
          record={workspace.record}
          onOpenVersion={async (artifactId, revisionId) => {
            setOpenError(null);
            try {
              await workspace.openArtifact(artifactId, revisionId);
            } catch (cause) {
              setOpenError(cause instanceof Error ? cause.message : 'That version could not be opened.');
            }
          }}
          onClose={close}
        />
      ) : (
        <>
          <header className="panel-header">
            <div>
              <h2>Artifact</h2>
              <p className="muted">Making something to keep</p>
            </div>
            <button type="button" className="icon-button" aria-label="Close artifact panel" onClick={close}>
              <CloseIcon />
            </button>
          </header>
          <div className="panel-body">
            {run ? (
              <RunCard
                item={run}
                onCancel={() => void workspace.cancelRun(run.localId)}
                onRetry={() => void workspace.retryRun(run.localId)}
                onDismiss={() => workspace.dismissRun(run.localId)}
              />
            ) : (
              <p className="muted">Pick an artifact from the sidebar or ask the chat to make one.</p>
            )}
            {openError && (
              <p className="form-error" role="alert">
                {openError}
              </p>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
