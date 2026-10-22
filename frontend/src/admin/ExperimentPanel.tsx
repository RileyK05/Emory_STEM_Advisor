import { useState } from 'react';
import { startDebugExperiment } from '../api/backoffice';
import type { DebugExperimentResponse } from '../api/types';
import { useAsyncAction } from '../lib/useAsyncAction';

export function ExperimentPanel() {
  const [conversationId, setConversationId] = useState('');
  const [requestId, setRequestId] = useState('');
  const [query, setQuery] = useState('');
  const [experiment, setExperiment] = useState<DebugExperimentResponse | null>(null);
  const { busy, error, notice, setNotice, run } = useAsyncAction();

  const start = () =>
    run(async () => {
      const row = await startDebugExperiment({
        conversationId: conversationId.trim(),
        requestId: requestId.trim(),
        snapshot: { query: query.trim() },
      });
      setExperiment(row);
      setNotice('Debug experiment recorded.');
    });

  return (
    <section className="stack">
      {!experiment && <p>No debug experiment is recorded.</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void start()}>
            Retry debug experiment
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      <label>
        Conversation id
        <input value={conversationId} onChange={(event) => setConversationId(event.target.value)} />
      </label>
      <label>
        Request id
        <input value={requestId} onChange={(event) => setRequestId(event.target.value)} />
      </label>
      <label>
        Saved query
        <input value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={busy || !conversationId.trim() || !requestId.trim() || !query.trim()}
        onClick={() => void start()}
      >
        Record debug experiment
      </button>
      {experiment && (
        <>
          <p>Experiment {experiment.experimentId} is not the conversation.</p>
          <p>Experiment is not part of the student conversation.</p>
        </>
      )}
    </section>
  );
}
