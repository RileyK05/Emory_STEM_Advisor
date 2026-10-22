import { useState } from 'react';
import { startDebugExperiment } from '../api/backoffice';
import type { DebugExperimentResponse } from '../api/types';
import { errorMessage } from '../lib/errors';

export function ExperimentPanel() {
  const [conversationId, setConversationId] = useState('');
  const [requestId, setRequestId] = useState('');
  const [query, setQuery] = useState('');
  const [experiment, setExperiment] = useState<DebugExperimentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await startDebugExperiment({
        conversationId: conversationId.trim(),
        requestId: requestId.trim(),
        snapshot: { query: query.trim() },
      });
      setExperiment(row);
      setNotice('Debug experiment recorded.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

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
