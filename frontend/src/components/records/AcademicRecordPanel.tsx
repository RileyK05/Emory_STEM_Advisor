import { useState } from 'react';
import { getClient } from '../../api/client';
import type { AcademicRecordAttemptItem, AcademicRecordImportBatchResponse } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { useLoad } from '../../lib/useLoad';

export function AcademicRecordPanel() {
  const [attempts, setAttempts] = useState<AcademicRecordAttemptItem[]>([]);
  const [revision, setRevision] = useState(1);
  const [course, setCourse] = useState('');
  const [term, setTerm] = useState('');
  const [grade, setGrade] = useState('');
  const [csv, setCsv] = useState('');
  const [batch, setBatch] = useState<AcademicRecordImportBatchResponse | null>(null);
  const [rowGrade, setRowGrade] = useState('');
  const [historical, setHistorical] = useState('');
  const { busy, error, notice, setError, setNotice, run } = useAsyncAction();
  const importDirty = batch !== null && rowGrade !== String(batch.parsedRows?.[0]?.grade ?? '');

  const { loading, reload: load } = useLoad(() => getClient().getAcademicRecord(), [], {
    onLoad: (row) => {
      setAttempts(row.attempts ?? []);
      setRevision(row.record.recordRevision);
    },
    setError,
  });

  const saveAttempt = () =>
    run(async () => {
      await getClient().upsertAcademicAttempt({
        submittedCourseCode: course.trim(),
        term: term.trim(),
        grade: grade.trim(),
        provenance: 'student_manual',
      });
      setNotice('Course attempt saved.');
      await load();
    });

  const preview = () =>
    run(
      async () => {
        const row = await getClient().previewAcademicImport({
          filename: 'courses.csv',
          rawContent: csv,
          expectedRecordRevision: revision,
        });
        setBatch(row);
        setRowGrade(String(row.parsedRows?.[0]?.grade ?? ''));
        setNotice('Import preview ready.');
      },
      { keepNotice: true },
    );

  const saveRows = async () => {
    if (!batch) return;
    await run(
      async () => {
        const updated = await getClient().updateAcademicImport(batch.batchId, {
          expectedRecordRevision: revision,
          corrections: [{ rowIndex: 0, grade: rowGrade }],
        });
        setBatch(updated);
        setNotice('Import rows saved.');
      },
      { keepNotice: true },
    );
  };

  const commit = async () => {
    const previewDigest = batch?.previewDigest;
    if (!batch || !previewDigest || importDirty) return;
    await run(
      async () => {
        const result = await getClient().commitAcademicImport(batch.batchId, {
          expectedRecordRevision: revision,
          previewDigest,
        });
        setNotice(`Import committed ${result.attemptsImported} attempt.`);
        await load();
      },
      { keepNotice: true },
    );
  };

  const showPrior = () =>
    run(
      async () => {
        const prior = await getClient().getAcademicRecordRevision(Math.max(1, revision - 1));
        const grades = (prior.attempts ?? []).map((row) => `${row.submittedCourseCode} ${row.grade ?? ''}`).join(', ');
        setHistorical(grades || 'No attempts in that revision.');
      },
      { keepNotice: true },
    );

  return (
    <section className="stack" aria-label="Academic record">
      <h3>Academic record</h3>
      {loading && <p>Loading academic record...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void load()}>
            Retry record
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      {!loading && !error && attempts.length === 0 && <p>No course attempts.</p>}
      <ul>
        {attempts.map((attempt) => (
          <li key={attempt.attemptId}>
            {attempt.submittedCourseCode} {attempt.term} {attempt.grade} {attempt.provenance}
          </li>
        ))}
      </ul>
      <label>
        Course code
        <input value={course} onChange={(event) => setCourse(event.target.value)} />
      </label>
      <label>
        Term
        <input value={term} onChange={(event) => setTerm(event.target.value)} />
      </label>
      <label>
        Grade
        <input value={grade} onChange={(event) => setGrade(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={busy || loading || !course.trim() || !term.trim()}
        onClick={() => void saveAttempt()}
      >
        Save attempt
      </button>
      <label>
        CSV import
        <textarea value={csv} onChange={(event) => setCsv(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !csv.trim()} onClick={() => void preview()}>
        Preview import
      </button>
      {batch && (
        <>
          <label>
            Reviewed grade
            <input value={rowGrade} onChange={(event) => setRowGrade(event.target.value)} />
          </label>
          <button type="button" disabled={busy} onClick={() => void saveRows()}>
            Save import rows
          </button>
          {importDirty && <p>Save the reviewed grade before committing the import.</p>}
          <button type="button" disabled={busy || importDirty || !batch.previewDigest} onClick={() => void commit()}>
            Commit import
          </button>
        </>
      )}
      <button type="button" disabled={busy} onClick={() => void showPrior()}>
        Show prior revision
      </button>
      {historical && <p>{historical}</p>}
    </section>
  );
}
