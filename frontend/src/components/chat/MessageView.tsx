import { memo, useState } from 'react';
import { getClient } from '../../api/client';
import { CheckIcon, CopyIcon, KindIcon, StopIcon, ThumbDownIcon, ThumbUpIcon } from '../icons';
import { AnswerText } from './AnswerText';
import { ApplicabilityBanner } from './ApplicabilityBanner';
import { SourceList } from './SourceList';
import { streamStageLabel } from './model';
import type { AssistantEntry, ChatEntry, StreamPreview } from './model';

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

function VoteButtons({ entry }: { entry: AssistantEntry }) {
  const [vote, setVote] = useState<'up' | 'down' | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const traceId = entry.responseId ?? entry.trace?.traceId;
  if (!traceId) return null;
  const submit = async (next: 'up' | 'down') => {
    if (sending) return;
    setSending(true);
    setError(null);
    try {
      await getClient().submitResponseFeedback({
        responseId: traceId,
        vote: next,
        reasonCode: next === 'up' ? 'helpful' : 'not_helpful',
      });
      setVote(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Feedback could not be saved.');
    } finally {
      setSending(false);
    }
  };
  return (
    <span className="vote-buttons">
      <button
        type="button"
        className="icon-button"
        aria-label="Helpful answer"
        aria-pressed={vote === 'up'}
        disabled={sending}
        onClick={() => void submit('up')}
      >
        <ThumbUpIcon size={16} />
      </button>
      <button
        type="button"
        className="icon-button"
        aria-label="Not a helpful answer"
        aria-pressed={vote === 'down'}
        disabled={sending}
        onClick={() => void submit('down')}
      >
        <ThumbDownIcon size={16} />
      </button>
      {error && <span className="form-error">{error}</span>}
    </span>
  );
}

interface AssistantProps {
  entry: AssistantEntry;
}

function AssistantMessage({ entry }: AssistantProps) {
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
          {entry.artifactNotice && (
            <p className="callout callout-info" role="status">
              {entry.artifactNotice}
            </p>
          )}
          <SourceList
            citations={entry.citations}
            context={entry.context}
            openNumber={openPassage}
            onToggle={setOpenPassage}
          />
          <div className="message-actions">
            <CopyButton text={entry.text} />
            <VoteButtons entry={entry} />
          </div>
        </>
      )}
    </div>
  );
}

export const MessageView = memo(function MessageView({ entry }: { entry: ChatEntry }) {
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
  return <AssistantMessage entry={entry} />;
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
