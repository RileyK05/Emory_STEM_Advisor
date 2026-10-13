import { useState } from 'react';
import type { NativeArtifactRecord } from '../../api/types';
import { errorMessage } from '../../lib/errors';
import { CloseIcon, FolderIcon, KIND_LABEL, KindIcon, PlusIcon } from '../icons';
import { ArtifactEditor } from './ArtifactEditor';
import { readableStage } from './labels';
import type { ArtifactWorkspace, RunItem } from './useArtifactWorkspace';
import { runOutputs } from './useArtifactWorkspace';

function RunCard({ item, onCancel, onDismiss }: { item: RunItem; onCancel: () => void; onDismiss: () => void }) {
  const lastStage = [...item.events].reverse().find((event) => event.stage)?.stage;
  const state = item.run.state;
  const active = state === 'queued' || state === 'running';
  const nothingSaved = state === 'succeeded' && runOutputs(item.run, item.events).length === 0;
  return (
    <li className={'artifact-card run-card' + (active ? ' run-active' : ' run-done')} role="status">
      <span className="artifact-card-icon">
        <KindIcon kind={item.kind} />
      </span>
      <div className="artifact-card-text">
        <strong>{item.label || `New ${KIND_LABEL[item.kind].toLowerCase()}`}</strong>
        <span className="muted">
          {active
            ? state === 'queued'
              ? 'Waiting to start…'
              : (readableStage(lastStage) ?? 'Working…')
            : state === 'failed'
              ? 'Couldn’t finish this draft.'
              : state === 'cancelled'
                ? 'Cancelled.'
                : nothingSaved
                  ? 'Finished, but nothing was saved.'
                  : 'Finishing…'}
        </span>
        {item.run.error && <span className="form-error">{item.run.error}</span>}
        {item.pollError && <span className="muted">Status check failed, retrying: {item.pollError}</span>}
        {active && <span className="progress" aria-hidden />}
      </div>
      {active ? (
        <button type="button" className="ghost-button" onClick={onCancel}>
          Cancel
        </button>
      ) : (
        <button type="button" className="icon-button" aria-label="Dismiss" onClick={onDismiss}>
          <CloseIcon size={16} />
        </button>
      )}
    </li>
  );
}

interface Props {
  workspace: ArtifactWorkspace;
  onNew: () => void;
  onLibrary: () => void;
  onAskChanges: (record: NativeArtifactRecord) => void;
  onDiscuss?: (record: NativeArtifactRecord) => void;
}

/** The chat's artifacts: jobs in progress, finished artifacts, and the canvas for the one that is open. */
export function ArtifactPanel({ workspace, onNew, onLibrary, onAskChanges, onDiscuss }: Props) {
  const [openError, setOpenError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);

  if (workspace.record)
    return (
      <aside className="artifact-panel artifact-panel-canvas" aria-label="Artifacts">
        <ArtifactEditor
          record={workspace.record}
          onRecord={(next) => workspace.showRecord(next)}
          onBack={workspace.closeArtifact}
          onClose={() => {
            workspace.closeArtifact();
            workspace.setPanelOpen(false);
          }}
          onAskChanges={onAskChanges}
          onDiscuss={onDiscuss}
        />
      </aside>
    );

  const open = async (artifactId: string, revisionId?: string) => {
    setOpening(artifactId);
    setOpenError(null);
    try {
      await workspace.openArtifact(artifactId, revisionId);
    } catch (cause) {
      setOpenError(errorMessage(cause));
    } finally {
      setOpening(null);
    }
  };

  const empty = workspace.links.length === 0 && workspace.runs.length === 0;
  return (
    <aside className="artifact-panel" aria-label="Artifacts">
      <header className="panel-header">
        <div>
          <h2>Artifacts</h2>
          <p className="muted">Made in this chat</p>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Close artifacts"
          onClick={() => workspace.setPanelOpen(false)}
        >
          <CloseIcon />
        </button>
      </header>
      <div className="panel-body">
        {empty && (
          <div className="panel-empty">
            <FolderIcon size={28} />
            <p>Nothing here yet.</p>
            <p className="muted">Use “Make a document” under an answer, or the + button next to the message box.</p>
          </div>
        )}
        <ul className="artifact-list">
          {workspace.runs.map((item) => (
            <RunCard
              key={item.localId}
              item={item}
              onCancel={() => void workspace.cancelRun(item.localId)}
              onDismiss={() => workspace.dismissRun(item.localId)}
            />
          ))}
          {workspace.links.map((link) => (
            <li key={link.artifactId}>
              <button
                type="button"
                className="artifact-card"
                disabled={opening !== null}
                onClick={() => void open(link.artifactId, link.revisionId)}
              >
                <span className="artifact-card-icon">
                  <KindIcon kind={link.kind} />
                </span>
                <span className="artifact-card-text">
                  <strong>{link.title}</strong>
                  <span className="muted">{opening === link.artifactId ? 'Opening…' : KIND_LABEL[link.kind]}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        {openError && (
          <p className="form-error" role="alert">
            {openError}
          </p>
        )}
      </div>
      <footer className="panel-footer">
        <button type="button" className="secondary-button" onClick={onNew}>
          <PlusIcon size={16} /> New
        </button>
        <button type="button" className="link-button" onClick={onLibrary}>
          All artifacts
        </button>
      </footer>
    </aside>
  );
}
