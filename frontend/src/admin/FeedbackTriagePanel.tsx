import { useState } from 'react';
import { listFeedbackTriage, promoteEvalCandidate, recordFeedbackTriage } from '../api/backoffice';
import type { EvalCandidatePromotionResponse, FeedbackTriageListItem, FeedbackTriageResponse } from '../api/types';
import { useAsyncAction } from '../lib/useAsyncAction';
import { useLoad } from '../lib/useLoad';
import { AdminSection } from './adminUi';

type IssueType = 'missing_evidence' | 'provider' | 'explanation' | 'privacy' | 'ui';

export function FeedbackTriagePanel() {
  const [items, setItems] = useState<FeedbackTriageListItem[] | null>(null);
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

  const action = useAsyncAction();
  const { error, notice, setNotice, run } = action;
  const { loading, reload } = useLoad(listFeedbackTriage, [], { onLoad: setItems, setError: action.setError });
  const busy = action.busy || loading;

  const load = () => {
    setLastAction('load');
    return reload();
  };

  const record = () => {
    setLastAction('record');
    return run(async () => {
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
    });
  };

  const promote = async () => {
    if (!triage) return;
    setLastAction('promote');
    await run(
      async () => {
        setCandidate(
          await promoteEvalCandidate({
            feedbackId: triage.feedbackId,
            redactedPrompt: redactedPrompt.trim(),
            redactedExpected: redactedExpected.trim(),
            sourceId: sourceId.trim(),
            traceId: traceId.trim(),
            collectionId: collectionId.trim(),
            reReview: false,
          }),
        );
        setNotice('Redacted eval candidate saved.');
      },
      { keepNotice: true },
    );
  };

  return (
    <AdminSection
      title="Feedback triage"
      description="Turn a student’s thumbs-down into an investigation, then save a redacted copy of the question as an evaluation candidate."
    >
      {busy && items === null && <p className="admin-note">Loading feedback triage...</p>}
      {items && items.length === 0 && !triage && <p className="admin-note">No feedback triage is recorded.</p>}
      {error && (
        <p role="alert" className="admin-error">
          {error}{' '}
          <button
            type="button"
            className="secondary-button"
            onClick={() => void (lastAction === 'promote' ? promote() : lastAction === 'record' ? record() : load())}
          >
            Retry feedback triage
          </button>
        </p>
      )}
      {notice && (
        <p className="admin-notice" role="status">
          {notice}
        </p>
      )}
      <div className="admin-subsection">
        <h3 className="admin-subhead">1. Record the triage</h3>
        <div className="admin-form admin-form-grid">
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
          <label className="admin-span-2">
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
          <label className="admin-span-2">
            Investigation query
            <input value={investigationQuery} onChange={(event) => setInvestigationQuery(event.target.value)} />
          </label>
          <div className="admin-form-actions">
            <button
              type="button"
              className="primary-button"
              disabled={busy || !feedbackId.trim()}
              onClick={() => void record()}
            >
              Record feedback triage
            </button>
          </div>
        </div>
        {triage && (
          <p className="admin-note">
            Issue {triage.issueType}. Triage revision {triage.triageRevision}.
            {triage.gapId ? ` Gap ${triage.gapId} is an investigation.` : ' No institutional fact was written.'}
          </p>
        )}
      </div>
      <div className="admin-subsection">
        <h3 className="admin-subhead">2. Save a redacted evaluation candidate</h3>
        <div className="admin-form admin-form-grid">
          <label className="admin-span-2">
            Source id
            <input value={sourceId} onChange={(event) => setSourceId(event.target.value)} />
          </label>
          <label className="admin-span-2">
            Redacted prompt
            <input value={redactedPrompt} onChange={(event) => setRedactedPrompt(event.target.value)} />
          </label>
          <label className="admin-span-2">
            Redacted expected
            <input value={redactedExpected} onChange={(event) => setRedactedExpected(event.target.value)} />
          </label>
          <div className="admin-form-actions">
            <button
              type="button"
              className="primary-button"
              disabled={busy || !triage || !sourceId.trim() || !redactedPrompt.trim() || !redactedExpected.trim()}
              onClick={() => void promote()}
            >
              Save redacted candidate
            </button>
          </div>
        </div>
        {candidate && (
          <p className="admin-note">
            Candidate {candidate.candidateId} stores no student history. Review {candidate.reviewState}.
          </p>
        )}
      </div>
    </AdminSection>
  );
}
