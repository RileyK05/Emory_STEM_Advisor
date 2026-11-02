import { useState } from 'react';
import type { AdminSource, ProgramInspectionResponse } from '../api/types';
import {
  approveProgramRelease,
  draftProgramRules,
  getProgramInspection,
  getSourceReview,
  listAdminSources,
  publishProgramRelease,
  rejectProgramRelease,
} from '../api/backoffice';
import { useAsyncAction } from '../lib/useAsyncAction';
import { useLoad } from '../lib/useLoad';

type ProgramRef = { programId: string; title: string };

/**
 * Program inspection and rule releases, in Courses & programs.
 *
 * A program reaches this panel through the catalog source it is bound to: the
 * source review lists the programs that source carries, and the reviewer picks
 * one. Nothing is inferred here; the panel only shows what the review routes
 * report.
 */
export function ProgramRulesPanel() {
  const [sources, setSources] = useState<AdminSource[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [programs, setPrograms] = useState<ProgramRef[]>([]);
  const [programId, setProgramId] = useState('');
  const [program, setProgram] = useState<ProgramInspectionResponse | null>(null);
  const [releaseId, setReleaseId] = useState('');
  const { busy, error, notice, setError, setNotice, run } = useAsyncAction();

  useLoad(listAdminSources, [], {
    onLoad: (rows) => setSources(rows.filter((row) => row.isCurrent)),
    setError,
  });

  const chooseSource = (id: string) => {
    setSourceId(id);
    setPrograms([]);
    setProgramId('');
    setProgram(null);
    setReleaseId('');
    setNotice(null);
    if (!id) return;
    void run(async () => {
      const detail = await getSourceReview(id);
      setPrograms(
        (detail.dependencies?.programs ?? [])
          .map((row) => ({
            programId: String(row.programId ?? row.program_id ?? ''),
            title: String(row.title ?? 'Program'),
          }))
          .filter((row) => row.programId),
      );
    });
  };

  const openProgram = (id: string) => {
    setProgramId(id);
    setReleaseId('');
    setNotice(null);
    void run(async () => setProgram(await getProgramInspection(id)));
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

  return (
    <section className="admin-review" aria-label="Program rules">
      <h3>Program rules</h3>
      {error && (
        <p role="alert">
          {error}{' '}
          <button
            type="button"
            className="secondary-button"
            disabled={!sourceId}
            onClick={() => chooseSource(sourceId)}
          >
            Retry program rules
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      <label className="admin-field-line">
        <span>Catalog source</span>
        <select value={sourceId} onChange={(event) => chooseSource(event.target.value)}>
          <option value="">Select a source</option>
          {sources.map((source) => (
            <option key={source.sourceId} value={source.sourceId}>
              {source.filename}
            </option>
          ))}
        </select>
      </label>
      {sourceId && !busy && programs.length === 0 && <p>No programs are bound to this source.</p>}
      {programs.length > 0 && (
        <div className="admin-actions">
          {programs.map((row) => (
            <button
              key={row.programId}
              type="button"
              className={row.programId === programId ? 'primary-button' : 'secondary-button'}
              disabled={busy}
              onClick={() => openProgram(row.programId)}
            >
              {row.title}
            </button>
          ))}
        </div>
      )}
      {programId && (
        <div className="admin-actions">
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
