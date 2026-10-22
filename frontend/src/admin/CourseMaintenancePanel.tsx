import { useEffect, useState } from 'react';
import type {
  AdminCourse,
  AdminCourseChange,
  AdminCourseDetail,
  AdminCourseListResponse,
  AdminCoursePreviewResponse,
} from '../api/types';
import {
  createCourseChange,
  getAdminCourse,
  listAdminCollections,
  listAdminCourses,
  listCourseConflicts,
  previewCourseChange,
  publishCourseChange,
  rejectCourseChange,
} from '../api/backoffice';
import { errorMessage } from '../lib/errors';
import { useAsyncAction } from '../lib/useAsyncAction';
import { useLoad } from '../lib/useLoad';

const LIFECYCLES = ['', 'current', 'retired'];
const AUTHORITIES = ['', 'atlas', 'catalog', 'admin_correction', 'admin_created'];
const CAMPUSES = ['', 'atlanta', 'oxford'];
const PAGE = 50;

export function CourseMaintenancePanel() {
  const [data, setData] = useState<AdminCourseListResponse | null>(null);
  const [detail, setDetail] = useState<AdminCourseDetail | null>(null);
  const [change, setChange] = useState<AdminCourseChange | null>(null);
  const [preview, setPreview] = useState<AdminCoursePreviewResponse | null>(null);
  const [conflicts, setConflicts] = useState<Array<{ changeId: string; code: string; conflictNote?: string | null }>>(
    [],
  );
  const [query, setQuery] = useState('');
  const [subject, setSubject] = useState('');
  const [lifecycle, setLifecycle] = useState('');
  const [authority, setAuthority] = useState('');
  const [campus, setCampus] = useState('');
  const [offset, setOffset] = useState(0);
  const [title, setTitle] = useState('');
  const [requisites, setRequisites] = useState('');
  const [reason, setReason] = useState('');
  const [newCourse, setNewCourse] = useState(false);
  const [newCode, setNewCode] = useState('');
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [collectionError, setCollectionError] = useState<string | null>(null);
  const { busy, error, notice, setError, setNotice, run } = useAsyncAction();

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const rows = await listAdminCollections(100, 0);
        if (!active) return;
        const preferred = rows.find((row) => row.name === 'default') ?? rows[0];
        setCollectionId(preferred?.collectionId ?? null);
      } catch (cause) {
        if (active) setCollectionError(errorMessage(cause));
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const { loading, reload: load } = useLoad(
    async () => {
      if (!collectionId) return null;
      const rows = await listAdminCourses({
        collection_id: collectionId,
        query,
        subject,
        lifecycle,
        authority,
        campus,
        limit: PAGE,
        offset,
      });
      return { rows, conflicts: await listCourseConflicts(collectionId) };
    },
    [collectionId, query, subject, lifecycle, authority, campus, offset],
    {
      enabled: collectionId !== null,
      onLoad: (loaded) => {
        if (!loaded) return;
        setData(loaded.rows);
        setConflicts(loaded.conflicts);
      },
      setError,
    },
  );

  const openCourse = (code: string) =>
    run(async () => {
      setChange(null);
      setPreview(null);
      setNewCourse(false);
      const row = await getAdminCourse(code, collectionId ?? undefined);
      setDetail(row);
      setTitle(row.course.title);
      setRequisites(row.course.requisitesRaw ?? '');
      setReason('');
    });

  const stage = (kind: 'create' | 'update' | 'retire' | 'restore') =>
    run(async () => {
      const created = await createCourseChange(
        {
          code: newCourse ? newCode : (detail?.course.code ?? ''),
          changeKind: kind,
          title,
          requisitesRaw: requisites,
          reason,
          expectedRevision: newCourse ? 0 : (detail?.course.adminRevision ?? 0),
        },
        collectionId ?? undefined,
      );
      setChange(created);
      setPreview(await previewCourseChange(created.changeId));
      setNotice('Draft staged. Review the preview before publishing.');
    });

  const publish = async () => {
    if (!change) return;
    await run(
      async () => {
        await publishCourseChange(change.changeId);
        setNotice('Course change published. Affected audits are marked stale.');
        setChange(null);
        setPreview(null);
        if (detail) await openCourse(detail.course.code);
        await load();
      },
      { keepNotice: true },
    );
  };

  const reject = async () => {
    if (!change) return;
    await run(
      async () => {
        await rejectCourseChange(change.changeId, reason || 'Rejected during review.');
        setNotice('Draft rejected.');
        setChange(null);
        setPreview(null);
      },
      { keepNotice: true },
    );
  };

  const courses: AdminCourse[] = data?.courses ?? [];
  const total = data?.total ?? 0;

  return (
    <section className="admin-review" aria-label="Course maintenance">
      <h3>Course maintenance</h3>
      <p className="admin-sub">
        Course edits are versioned. A manual correction keeps <span className="mono">admin_correction</span> authority
        and never claims to be an Atlas statement. Prerequisites are re-parsed by the deterministic parser; unresolved
        wording is shown without invented edges.
      </p>

      {loading && <p>Loading courses...</p>}
      {collectionError && <p role="alert">Could not resolve a course collection: {collectionError}</p>}
      {!collectionId && !collectionError && <p className="admin-sub">Resolving the course collection...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" className="secondary-button" onClick={() => void load()}>
            Retry course list
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}

      <div className="admin-filters">
        <label className="admin-field-line">
          <span>Search code or title</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <label className="admin-field-line">
          <span>Subject</span>
          <select value={subject} onChange={(event) => setSubject(event.target.value)}>
            <option value="">All subjects</option>
            {(data?.subjects ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="admin-field-line">
          <span>Lifecycle</span>
          <select value={lifecycle} onChange={(event) => setLifecycle(event.target.value)}>
            {LIFECYCLES.map((value) => (
              <option key={value || 'all'} value={value}>
                {value || 'Any'}
              </option>
            ))}
          </select>
        </label>
        <label className="admin-field-line">
          <span>Authority</span>
          <select value={authority} onChange={(event) => setAuthority(event.target.value)}>
            {AUTHORITIES.map((value) => (
              <option key={value || 'all'} value={value}>
                {value || 'Any'}
              </option>
            ))}
          </select>
        </label>
        <label className="admin-field-line">
          <span>Campus</span>
          <select value={campus} onChange={(event) => setCampus(event.target.value)}>
            {CAMPUSES.map((value) => (
              <option key={value || 'all'} value={value}>
                {value || 'Any'}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="admin-sub">
        {total} course identities. Page {Math.floor(offset / PAGE) + 1}.
        <button
          type="button"
          className="secondary-button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE))}
        >
          Previous
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
        >
          Next
        </button>
      </p>

      {conflicts.length > 0 && (
        <div role="status">
          <h4>Baseline conflicts awaiting review</h4>
          <ul>
            {conflicts.map((row) => (
              <li key={row.changeId}>
                <span className="mono">{row.code}</span> —{' '}
                {row.conflictNote ?? 'Baseline disagrees with a published correction.'}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Title</th>
              <th>Lifecycle</th>
              <th>Authority</th>
              <th>Rev</th>
              <th>Unresolved</th>
              <th>Programs</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {courses.map((course) => (
              <tr key={course.courseId}>
                <td className="mono">{course.code}</td>
                <td>{course.title}</td>
                <td>{course.lifecycle}</td>
                <td>{course.authority}</td>
                <td>{course.adminRevision}</td>
                <td>{course.unresolvedCount}</td>
                <td>{course.programCount}</td>
                <td>
                  <button type="button" className="secondary-button" onClick={() => void openCourse(course.code)}>
                    Review
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>Add or edit an identity</h4>
      <label className="admin-field-line">
        <span>
          <input
            type="checkbox"
            checked={newCourse}
            onChange={(event) => {
              setNewCourse(event.target.checked);
              setChange(null);
              setPreview(null);
            }}
          />{' '}
          New course (requires an indexed source to anchor it)
        </span>
      </label>
      {newCourse ? (
        <label className="admin-field-line">
          <span>New code</span>
          <input value={newCode} onChange={(event) => setNewCode(event.target.value)} placeholder="MATH 111" />
        </label>
      ) : (
        <p className="admin-sub">
          {detail ? (
            <>
              Editing <span className="mono">{detail.course.code}</span> at revision {detail.course.adminRevision},
              authority {detail.course.authority}.
            </>
          ) : (
            'Choose a course above to edit it.'
          )}
        </p>
      )}
      <label className="admin-field-line">
        <span>Title</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label className="admin-field-line">
        <span>Raw requisite wording</span>
        <textarea rows={4} value={requisites} onChange={(event) => setRequisites(event.target.value)} />
      </label>
      <label className="admin-field-line">
        <span>Edit reason (required)</span>
        <input value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>

      <p>
        <button
          type="button"
          className="secondary-button"
          disabled={busy || !reason.trim()}
          onClick={() => void stage(newCourse ? 'create' : 'update')}
        >
          {newCourse ? 'Stage new course' : 'Stage correction'}
        </button>
        {detail && detail.course.lifecycle === 'current' && !newCourse && (
          <button
            type="button"
            className="secondary-button"
            disabled={busy || !reason.trim()}
            onClick={() => void stage('retire')}
          >
            Stage retirement
          </button>
        )}
        {detail && detail.course.lifecycle === 'retired' && !newCourse && (
          <button
            type="button"
            className="secondary-button"
            disabled={busy || !reason.trim()}
            onClick={() => void stage('restore')}
          >
            Stage restoration
          </button>
        )}
      </p>

      {preview && (
        <div className="admin-review" role="region" aria-label="Publication preview">
          <h4>Publication preview</h4>
          <p>
            Changed fields: <span className="mono">{preview.changedFields.join(', ') || 'none'}</span>
          </p>
          {preview.parsed && (
            <p>
              Deterministic parse: <span className="mono">{preview.parsed.status}</span>
              {preview.parsed.unresolved.length > 0 && (
                <>
                  {' '}
                  — unresolved <span className="mono">{preview.parsed.unresolved.join(', ')}</span>
                </>
              )}
            </p>
          )}
          {preview.dependents.length > 0 && (
            <p>
              Dependent courses: <span className="mono">{preview.dependents.map((row) => row.code).join(', ')}</span>
            </p>
          )}
          <p>
            {preview.staleAuditCount} saved audit(s) will be marked stale; {preview.activeTopicBuilds} active topic
            build(s) will be retired.
          </p>
          {preview.warnings.map((warning) => (
            <p key={warning} role="status">
              {warning}
            </p>
          ))}
          <p>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => void publish()}>
              Publish
            </button>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => void reject()}>
              Reject draft
            </button>
          </p>
        </div>
      )}

      {detail && (
        <div className="admin-review" role="region" aria-label="Course detail">
          <h4>
            {detail.course.code} — {detail.course.title}
          </h4>
          <p className="admin-sub">
            Lifecycle {detail.course.lifecycle} · authority {detail.course.authority} · revision{' '}
            {detail.course.adminRevision} · parse {detail.course.parseStatus}
          </p>
          <p>
            Source: {detail.course.filename ?? '—'}
            {detail.course.publisher ? ` · ${detail.course.publisher}` : ''}
            {detail.course.sourceUrl ? ` · ${detail.course.sourceUrl}` : ''}
          </p>
          <p>Requisites: {detail.course.requisitesRaw || '—'}</p>
          <p>
            Resolved prerequisites:{' '}
            {detail.mentions
              .filter((row) => row.resolved)
              .map((row) => row.targetCode)
              .join(', ') || '—'}
          </p>
          <p>
            Unresolved prerequisites:{' '}
            {detail.mentions
              .filter((row) => !row.resolved)
              .map((row) => row.targetCode)
              .join(', ') || '—'}
          </p>
          <p>Referenced by programs: {detail.programs.map((row) => row.title).join('; ') || '—'}</p>
          <p>Required by courses: {detail.dependents.map((row) => row.code).join(', ') || '—'}</p>
          <h5>Change history</h5>
          {detail.changes.length === 0 ? (
            <p>No recorded changes.</p>
          ) : (
            <ul>
              {detail.changes.map((row) => (
                <li key={row.changeId}>
                  <span className="mono">{row.changeKind}</span> · {row.status} · rev{' '}
                  {row.publishedRevision ?? row.expectedRevision} · {row.reason}
                  {row.conflictNote ? ` — ${row.conflictNote}` : ''}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
