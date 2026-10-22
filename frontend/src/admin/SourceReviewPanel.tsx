import { useCallback, useEffect, useRef, useState } from 'react';
import type { ProgramInspectionResponse, SourceReviewDetailResponse } from '../api/types';
import {
  approveProgramRelease,
  approveSourceProposal,
  changeSourceLifecycle,
  createSourceProposal,
  draftProgramRules,
  getProgramInspection,
  getSourceReview,
  getSourceRevisionEvidence,
  publishProgramRelease,
  rejectProgramRelease,
  rejectSourceProposal,
} from '../api/backoffice';
import { errorMessage } from '../lib/errors';

const CATEGORIES = [
  'institutional_catalog',
  'institutional_department',
  'institutional_policy',
  'institutional_publication',
  'career_reference',
];

export function SourceReviewPanel({ sourceId, indexRevision }: { sourceId: string; indexRevision: number }) {
  const [detail, setDetail] = useState<SourceReviewDetailResponse | null>(null);
  const [program, setProgram] = useState<ProgramInspectionResponse | null>(null);
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('institutional_policy');
  const [proposalId, setProposalId] = useState('');
  const [releaseId, setReleaseId] = useState('');
  const [evidenceText, setEvidenceText] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const row = await getSourceReview(sourceId);
      if (seq !== requestSeq.current) return;
      setDetail(row);
      const pending = row.pendingProposal;
      if (pending?.proposalId) setProposalId(pending.proposalId);
    } catch (cause) {
      if (seq === requestSeq.current) setError(errorMessage(cause));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [sourceId]);

  useEffect(() => {
    void load();
    return () => {
      requestSeq.current += 1;
    };
  }, [load]);

  const metadataRevision = Number(detail?.source?.metadataRevision ?? 1);
  const programId = String(detail?.dependencies?.programs?.[0]?.programId ?? '');

  const propose = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await createSourceProposal(sourceId, {
        expectedMetadataRevision: metadataRevision,
        sourceCategory: category,
        title: title.trim(),
        lifecycleStatus: 'active',
      });
      setProposalId(row.proposalId);
      setNotice('Source proposal saved.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await approveSourceProposal(sourceId, proposalId);
      setNotice('Source proposal approved.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    setBusy(true);
    setError(null);
    try {
      await rejectSourceProposal(sourceId, proposalId, 'Not the catalog of record');
      setNotice('Source proposal rejected.');
      setProposalId('');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const deprecate = async () => {
    setBusy(true);
    setError(null);
    try {
      await changeSourceLifecycle(sourceId, {
        action: 'deprecate',
        expectedMetadataRevision: metadataRevision,
        notes: 'Replaced by a later catalog',
      });
      setNotice('Source deprecated.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const showEvidence = async () => {
    setBusy(true);
    setError(null);
    try {
      const evidence = await getSourceRevisionEvidence(sourceId, indexRevision);
      setEvidenceText(
        evidence.passages?.map((row) => row.text).join('\n') || 'No preserved evidence for this revision.',
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const openProgram = async () => {
    if (!programId) return;
    setBusy(true);
    setError(null);
    try {
      setProgram(await getProgramInspection(programId));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const draft = async () => {
    setBusy(true);
    setError(null);
    try {
      const row = await draftProgramRules(programId, 'Reviewed clauses');
      setReleaseId(row.releaseId);
      setNotice('Rule draft saved.');
      setProgram(await getProgramInspection(programId));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const approveRelease = async () => {
    setBusy(true);
    setError(null);
    try {
      await approveProgramRelease(programId, releaseId);
      setNotice('Rule release approved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const publishRelease = async () => {
    setBusy(true);
    setError(null);
    try {
      await publishProgramRelease(programId, releaseId);
      setNotice('Rule release published.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const rejectRelease = async () => {
    setBusy(true);
    setError(null);
    try {
      await rejectProgramRelease(programId, releaseId, 'Ambiguous clause');
      setNotice('Rule release rejected.');
      setReleaseId('');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const proposals = detail?.proposals ?? [];

  return (
    <section className="admin-review" aria-label="Source review">
      <h3>Source review</h3>
      {loading && <p>Loading source review...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" className="secondary-button" onClick={() => void load()}>
            Retry source review
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      {!loading && !error && proposals.length === 0 && <p>No source proposals.</p>}
      <label className="admin-field-line">
        <span>Proposed title</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label className="admin-field-line">
        <span>Source category</span>
        <select value={category} onChange={(event) => setCategory(event.target.value)}>
          {CATEGORIES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <div className="admin-actions">
        <button
          type="button"
          className="primary-button"
          disabled={busy || loading || !title.trim()}
          onClick={() => void propose()}
        >
          Propose source change
        </button>
        <button type="button" className="primary-button" disabled={busy || !proposalId} onClick={() => void approve()}>
          Approve proposal
        </button>
        <button type="button" className="secondary-button" disabled={busy || !proposalId} onClick={() => void reject()}>
          Reject proposal
        </button>
        <button type="button" className="danger-button" disabled={busy || loading} onClick={() => void deprecate()}>
          Deprecate source
        </button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void showEvidence()}>
          Show revision evidence
        </button>
      </div>
      {evidenceText && <pre className="admin-json">{evidenceText}</pre>}
      {programId && (
        <div className="admin-actions">
          <button type="button" className="secondary-button" disabled={busy} onClick={() => void openProgram()}>
            Open program review
          </button>
          <button type="button" className="primary-button" disabled={busy || !program} onClick={() => void draft()}>
            Draft rules
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={busy || !releaseId}
            onClick={() => void approveRelease()}
          >
            Approve release
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={busy || !releaseId}
            onClick={() => void publishRelease()}
          >
            Publish release
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={busy || !releaseId}
            onClick={() => void rejectRelease()}
          >
            Reject release
          </button>
        </div>
      )}
      {program && (
        <ul>
          {(program.items ?? []).map((item) => (
            <li key={item.position}>{item.text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
