import { useState } from 'react';
import { getClient } from '../../api/client';
import type { ResponseFeedbackResponse } from '../../api/types';
import { errorMessage } from '../../lib/errors';

export function FeedbackPanel() {
  const [responseId, setResponseId] = useState('');
  const [vote, setVote] = useState<'up' | 'down'>('up');
  const [reasonCode, setReasonCode] = useState('');
  const [comment, setComment] = useState('');
  const [saved, setSaved] = useState<ResponseFeedbackResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await getClient().submitResponseFeedback({
        responseId: responseId.trim(),
        vote,
        reasonCode: reasonCode.trim(),
        comment: comment.trim() || null,
      });
      setSaved(row);
      setNotice('Feedback saved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack">
      {!saved && <p>No feedback submitted.</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void submit()}>
            Retry feedback
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      <label>
        Response id
        <input value={responseId} onChange={(event) => setResponseId(event.target.value)} />
      </label>
      <label>
        Vote
        <select value={vote} onChange={(event) => setVote(event.target.value as 'up' | 'down')}>
          <option value="up">Up</option>
          <option value="down">Down</option>
        </select>
      </label>
      <label>
        Reason code
        <input value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} />
      </label>
      <label>
        Comment
        <input value={comment} onChange={(event) => setComment(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !responseId.trim() || !reasonCode.trim()} onClick={() => void submit()}>
        Save feedback
      </button>
      {saved && (
        <p>
          Revision {saved.revision}. Current votes {saved.currentVotes}.
        </p>
      )}
    </section>
  );
}
