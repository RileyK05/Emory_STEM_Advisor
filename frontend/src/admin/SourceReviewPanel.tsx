import { useState } from 'react';
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
import { useAsyncAction } from '../lib/useAsyncAction';
import { useLoad } from '../lib/useLoad';

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
  const { busy, error, notice, setError, setNotice, run } = useAsyncAction();
  const { loading, reload: load } = useLoad(() => getSourceReview(sourceId), [sourceId], {
    onLoad: (row) => {
      setDetail(row);
      const pending = row.pendingProposal;
      if (pending?.proposalId) setProposalId(pending.proposalId);
    },
    setError,
  });

  const metadataRevision = Number(detail?.source?.metadataRevision ?? 1);
  const programId = String(detail?.dependencies?.programs?.[0]?.programId ?? '');

  const propose = () =>
    run(async () => {
      const row = await createSourceProposal(sourceId, {
        expectedMetadataRevision: metadataRevision,
        sourceCategory: category,
        title: title.trim(),
        lifecycleStatus: 'active',
      });
      setProposalId(row.proposalId);
      setNotice('Source proposal saved.');
      await load();
    });

  const approve = () =>
    run(async () => {
      await approveSourceProposal(sourceId, proposalId);
      setNotice('Source proposal approved.');
      await load();
    });

  const reject = () =>
    run(async () => {
      await rejectSourceProposal(sourceId, proposalId, 'Not the catalog of record');
      setNotice('Source proposal rejected.');
      setProposalId('');
      await load();
    });

  const deprecate = () =>
    run(async () => {
      await changeSourceLifecycle(sourceId, {
        action: 'deprecate',
        expectedMetadataRevision: metadataRevision,
        notes: 'Replaced by a later catalog',
      });
      setNotice('Source deprecated.');
      await load();
    });

  const showEvidence = () =>
    run(async () => {
      const evidence = await getSourceRevisionEvidence(sourceId, indexRevision);
      setEvidenceText(
        evidence.passages?.map((row) => row.text).join('\n') || 'No preserved evidence for this revision.',
      );
    });

  const openProgram = async () => {
    if (!programId) return;
    await run(async () => setProgram(await getProgramInspection(programId)));
  };

  const draft = () =>
    run(async () => {
      const row = await draftProgramRules(programId, 'Reviewed clauses');
      setReleaseId(row.releaseId);
      setNotice('Rule draft saved.');
      setProgram(await getProgramInspection(programId));
    });

  const approveRelease = () =>
    run(async () => {
      await approveProgramRelease(programId, releaseId);
      setNotice('Rule release approved.');
    });

  const publishRelease = () =>
    run(async () => {
      await publishProgramRelease(programId, releaseId);
      setNotice('Rule release published.');
    });

  const rejectRelease = () =>
    run(async () => {
      await rejectProgramRelease(programId, releaseId, 'Ambiguous clause');
      setNotice('Rule release rejected.');
      setReleaseId('');
    });

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
