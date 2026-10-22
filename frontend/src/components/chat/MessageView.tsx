import { memo, useState } from 'react';
import type { NativeArtifactKind } from '../../api/types';
import { useApp } from '../../state/AppContext';
import { CheckIcon, CopyIcon, KIND_LABEL, KindIcon, StopIcon } from '../icons';
import { AnswerText } from './AnswerText';
import { ApplicabilityBanner } from './ApplicabilityBanner';
import { DebugPanel } from './DebugPanel';
import { SourceList } from './SourceList';
import { streamStageLabel } from './model';
import type { AssistantEntry, ChatEntry, StreamPreview } from './model';

const MAKE_KINDS: NativeArtifactKind[] = ['document', 'workbook', 'html'];

function Dots() {
  return (
    <span className="dots" aria-hidden>
      <span />
      <span />
      <span />
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="ghost-button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <CheckIcon size={16} /> : <CopyIcon size={16} />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

interface AssistantProps {
  entry: AssistantEntry;
  /** Offer "make a document/spreadsheet/web page" from this answer. */
  onMake?: (kind: NativeArtifactKind, entry: AssistantEntry) => void;
}

function AssistantMessage({ entry, onMake }: AssistantProps) {
  const { showDetails } = useApp();
  const [openPassage, setOpenPassage] = useState<number | null>(null);

  if (entry.kind === 'thinking')
    return (
      <div className="message message-assistant" aria-label="Advisor is thinking">
        <div className="thinking">
          <Dots /> Searching your sources…
        </div>
      </div>
    );

  return (
    <div className="message message-assistant">
      {entry.kind === 'error' && (
        <div className="callout callout-error" role="alert">
          <strong>Something went wrong.</strong>
          <p>{entry.text}</p>
        </div>
      )}
      {entry.kind === 'refusal' && (
        <div className="callout callout-refusal">
          <strong>I couldn’t answer that from your sources.</strong>
          <p>{entry.text}</p>
          {Array.isArray(entry.scope) && entry.scope.length === 0 && (
            <p className="muted">No sources were selected. Choose some under “Search in” below the message box.</p>
          )}
        </div>
      )}
      {entry.kind === 'answer' && (
        <>
          {entry.applicability && <ApplicabilityBanner notice={entry.applicability} />}
          {entry.research?.completeness === 'partial' && (
            <div className="callout" role="note">
              <strong>Some questions remain open.</strong>
              <p>This advice is based on a partial investigation.</p>
              {entry.research.unresolvedQuestions.map((question, index) => (
                <p key={index}>{question}</p>
              ))}
            </div>
          )}
          <AnswerText
            text={entry.text}
            context={entry.context}
            onCite={(n) => setOpenPassage((current) => (current === n ? null : n))}
          />
          <SourceList
            citations={entry.citations}
            context={entry.context}
            openNumber={openPassage}
            onToggle={setOpenPassage}
          />
          <div className="message-actions">
            <CopyButton text={entry.text} />
            {onMake && (
              <div className="make-group" role="group" aria-label="Make something from this answer">
                <span className="make-label">Make a</span>
                {MAKE_KINDS.map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    className="ghost-button"
                    aria-label={`Make a ${KIND_LABEL[kind].toLowerCase()} from this answer`}
                    onClick={() => onMake(kind, entry)}
                  >
                    <KindIcon kind={kind} size={16} />
                    {KIND_LABEL[kind].toLowerCase()}
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
      {showDetails && entry.trace && <DebugPanel trace={entry.trace} />}
    </div>
  );
}

export const MessageView = memo(function MessageView({
  entry,
  onMake,
}: {
  entry: ChatEntry;
  onMake?: AssistantProps['onMake'];
}) {
  if (entry.role === 'user')
    return (
      <div className="message message-user">
        <div className="bubble">{entry.text}</div>
        {entry.attachments.length > 0 && (
          <div className="attachment-row">
            {entry.attachments.map((item) => (
              <span className="attachment-chip" key={`${item.artifactId}:${item.revisionId}`}>
                <KindIcon kind={item.kind} size={14} />
                {item.title}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  return <AssistantMessage entry={entry} onMake={onMake} />;
});

/** The answer to a saved question that is still being written: unverified text, and a way to stop it. */
export function PendingAnswer({
  preview,
  abandoning,
  onStop,
}: {
  preview: StreamPreview | null;
  abandoning: boolean;
  onStop: () => void;
}) {
  return (
    <div className="message message-assistant message-pending" role="status">
      <div className="thinking">
        <Dots /> {streamStageLabel(preview?.stage ?? null)}
      </div>
      {preview?.text && (
        <div className="provisional">
          <span className="provisional-label">Draft — sources are still being checked</span>
          <p aria-label="Unverified provisional answer">{preview.text}</p>
        </div>
      )}
      <button type="button" className="ghost-button" onClick={onStop} disabled={abandoning}>
        <StopIcon size={14} />
        {abandoning ? 'Stopping…' : 'Stop answering'}
      </button>
    </div>
  );
}
