import { useState } from 'react';
import { getClient } from '../../api/client';
import type { AuditableProgramItem, ProgramComparisonResponse, ProgramComparisonSummary } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { useLoad } from '../../lib/useLoad';

export function ProgramComparisonPanel() {
  const [programs, setPrograms] = useState<AuditableProgramItem[]>([]);
  const [saved, setSaved] = useState<ProgramComparisonSummary[]>([]);
  const [firstId, setFirstId] = useState('');
  const [secondId, setSecondId] = useState('');
  const [overlayCourse, setOverlayCourse] = useState('');
  const [detail, setDetail] = useState<ProgramComparisonResponse | null>(null);
  const { busy, error, notice, setError, setNotice, run } = useAsyncAction();

  const { loading, reload: load } = useLoad(
    () => Promise.all([getClient().listAuditablePrograms(), getClient().listProgramComparisons()]),
    [],
    {
      onLoad: ([programRows, comparisons]) => {
        setPrograms(programRows);
        setSaved(comparisons);
      },
      setError,
    },
  );

  const compare = () =>
    run(async () => {
      const row = await getClient().createProgramComparison({
        programIds: [firstId, secondId],
        addCourseCodes: overlayCourse.trim() ? [overlayCourse.trim()] : [],
      });
      setDetail(row);
      setNotice('Program comparison saved.');
      await load();
    });

  return (
    <section className="stack">
      <p>Mention overlap is not saved credit.</p>
      {loading && <p>Loading program comparisons...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void load()}>
            Retry program comparisons
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      {!loading && !error && saved.length === 0 && <p>No saved program comparisons.</p>}
      <ul>
        {saved.map((row) => (
          <li key={row.comparisonId}>
            {row.mentionKind} {row.sharingPolicy}
            {row.creditSavings == null ? ' no credit savings' : ''}
          </li>
        ))}
      </ul>
      <label>
        First program
        <select value={firstId} onChange={(event) => setFirstId(event.target.value)}>
          <option value="">Select a program</option>
          {programs.map((program) => (
            <option key={program.programId} value={program.programId}>
              {program.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        Second program
        <select value={secondId} onChange={(event) => setSecondId(event.target.value)}>
          <option value="">Select a program</option>
          {programs.map((program) => (
            <option key={program.programId} value={program.programId}>
              {program.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        Overlay course
        <input value={overlayCourse} onChange={(event) => setOverlayCourse(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={busy || loading || !firstId || !secondId || firstId === secondId}
        onClick={() => void compare()}
      >
        Compare programs
      </button>
      {detail && (
        <>
          <p>Sharing policy is {detail.sharingPolicy}.</p>
          {detail.creditSavings == null && <p>No credit savings are claimed.</p>}
          {(detail.sharedMentions ?? []).length > 0 && (
            <p>Shared mentions: {(detail.sharedMentions ?? []).join(', ')}</p>
          )}
          {detail.rankingBlockedReason && <p>{detail.rankingBlockedReason}</p>}
        </>
      )}
    </section>
  );
}
