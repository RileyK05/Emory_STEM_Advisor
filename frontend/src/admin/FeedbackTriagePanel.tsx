import { useEffect, useState } from 'react';
import { listFeedbackTriage, promoteEvalCandidate, recordFeedbackTriage } from '../api/backoffice';
import type { EvalCandidatePromotionResponse, FeedbackTriageListItem, FeedbackTriageResponse } from '../api/types';
import { errorMessage } from '../lib/errors';

type IssueType = 'missing_evidence' | 'provider' | 'explanation' | 'privacy' | 'ui';

export function FeedbackTriagePanel() {
  const [items, setItems] = useState<FeedbackTriageListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [feedbackId, setFeedbackId] = useState('');
  const [issueType, setIssueType] = useState<IssueType>('missing_evidence');
  const [note, setNote] = useState('');
  const [expectedRevision] = useState('1');
  const [expectedTriageRevision, setExpectedTriageRevision] = useState('0');
  const [traceId, setTraceId] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [investigationQuery, setInvestigationQuery] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [redactedPrompt, setRedactedPrompt] = useState('');
  const [redactedExpected, setRedactedExpected] = useState('');
  const [triage, setTriage] = useState<FeedbackTriageResponse | null>(null);
  const [candidate, setCandidate] = useState<EvalCandidatePromotionResponse | null>(null);
  const [lastAction, setLastAction] = useState<'load' | 'record' | 'promote'>('load');

  const load = async () => {
    setLastAction('load');
    setBusy(true);
    setError(null);
    try {
      setItems(await listFeedbackTriage());
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const record = async () => {
    setLastAction('record');
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await recordFeedbackTriage({
        feedbackId: feedbackId.trim(),
        issueType,
        toStatus: 'reviewed',
        expectedRevision: Number(expectedRevision),
        expectedTriageRevision: Number(expectedTriageRevision),
        note: note.trim() || null,
        traceId: traceId.trim() || null,
        collectionId: collectionId.trim() || null,
        investigationQuery: investigationQuery.trim() || null,
      });
      setTriage(row);
      setExpectedTriageRevision(String(row.triageRevision));
      setNotice('Feedback triage recorded.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const promote = async () => {
    if (!triage) return;
    setLastAction('promote');
    setBusy(true);
    setError(null);
    try {
      const row = await promoteEvalCandidate({
        feedbackId: triage.feedbackId,
        redactedPrompt: redactedPrompt.trim(),
        redactedExpected: redactedExpected.trim(),
        sourceId: sourceId.trim(),
        traceId: traceId.trim(),
        collectionId: collectionId.trim(),
        reReview: false,
      });
      setCandidate(row);
      setNotice('Redacted eval candidate saved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack">
      {busy && items === null && <p>Loading feedback triage...</p>}
      {items && items.length === 0 && !triage && <p>No feedback triage is recorded.</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button
            type="button"
            onClick={() => void (lastAction === 'promote' ? promote() : lastAction === 'record' ? record() : load())}
          >
            Retry feedback triage
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      <label>
        Feedback id
        <input value={feedbackId} onChange={(event) => setFeedbackId(event.target.value)} />
      </label>
      <label>
        Issue type
        <select value={issueType} onChange={(event) => setIssueType(event.target.value as IssueType)}>
          <option value="missing_evidence">missing evidence</option>
          <option value="provider">provider</option>
          <option value="explanation">explanation</option>
          <option value="privacy">privacy</option>
          <option value="ui">ui</option>
        </select>
      </label>
      <label>
        Triage note
        <input value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
      <label>
        Trace id
        <input value={traceId} onChange={(event) => setTraceId(event.target.value)} />
      </label>
      <label>
        Collection id
        <input value={collectionId} onChange={(event) => setCollectionId(event.target.value)} />
      </label>
      <label>
        Investigation query
        <input value={investigationQuery} onChange={(event) => setInvestigationQuery(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !feedbackId.trim()} onClick={() => void record()}>
        Record feedback triage
      </button>
      {triage && (
        <p>
          Issue {triage.issueType}. Triage revision {triage.triageRevision}.
          {triage.gapId ? ` Gap ${triage.gapId} is an investigation.` : ' No institutional fact was written.'}
        </p>
      )}
      <label>
        Source id
        <input value={sourceId} onChange={(event) => setSourceId(event.target.value)} />
      </label>
      <label>
        Redacted prompt
        <input value={redactedPrompt} onChange={(event) => setRedactedPrompt(event.target.value)} />
      </label>
      <label>
        Redacted expected
        <input value={redactedExpected} onChange={(event) => setRedactedExpected(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={busy || !triage || !sourceId.trim() || !redactedPrompt.trim() || !redactedExpected.trim()}
        onClick={() => void promote()}
      >
        Save redacted candidate
      </button>
      {candidate && (
        <p>
          Candidate {candidate.candidateId} stores no student history. Review {candidate.reviewState}.
        </p>
      )}
    </section>
  );
}
