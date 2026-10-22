import { useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type {
  ArtifactRunLimits,
  ArtifactRunRequest,
  NativeArtifactKind,
  NativeArtifactRecord,
  NativeArtifactSpec,
} from '../../api/types';
import { errorMessage, statusOf } from '../../lib/errors';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { useIdempotencyKey } from '../../lib/useIdempotencyKey';
import { useApp } from '../../state/AppContext';
import type { SourceScope } from '../../state/AppContext';
import { Dialog } from '../shell/Dialog';
import { KIND_LABEL, KindIcon, SparkIcon, UploadIcon } from '../icons';
import { scopeLabel } from '../chat/ScopePicker';
import { parseMarkdown } from './markdown';
import { DEFAULT_SECTION, DEFAULT_SHEET, buildOutputPlan } from './outputPlan';
import type { DocSectionDraft, SheetDraft } from './outputPlan';
import { DocumentPlanEditor, PlanLimitsEditor, WorkbookPlanEditor } from './PlanEditor';
import type { StartRunInput } from './useArtifactWorkspace';
import { emptyWorkbook } from './workbook';

type NewArtifactTab = 'draft' | 'blank' | 'import';

export interface NewArtifactRequest {
  /** Changes on every open so the form starts fresh. */
  nonce: number;
  tab: NewArtifactTab;
  kind: NativeArtifactKind;
  prompt?: string;
  /** The scope a draft is grounded in: the answer's, or the chat's current one. */
  scope?: SourceScope;
  /** Revise this artifact instead of creating a new one. */
  revise?: NativeArtifactRecord;
}

interface Props {
  request: NewArtifactRequest | null;
  onClose: () => void;
  onStartRun: (input: Omit<StartRunInput, 'chatKey'>) => Promise<void>;
  onCreated: (record: NativeArtifactRecord, notice?: string) => Promise<void> | void;
  /** Reload the latest version of the artifact being revised. */
  onReloadRevise: (artifactId: string) => Promise<NativeArtifactRecord>;
}

const KINDS: NativeArtifactKind[] = ['document', 'workbook', 'html'];
const BLANK_TITLES: Record<NativeArtifactKind, string> = {
  document: 'Untitled document',
  workbook: 'Untitled spreadsheet',
  html: 'Untitled web page',
};

function KindPicker({
  value,
  onChange,
  disabled,
}: {
  value: NativeArtifactKind;
  onChange: (kind: NativeArtifactKind) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="segmented" disabled={disabled}>
      <legend>Type</legend>
      {KINDS.map((kind) => (
        <label key={kind} className={value === kind ? 'segmented-on' : ''}>
          <input type="radio" name="artifact-kind" checked={value === kind} onChange={() => onChange(kind)} />
          <KindIcon kind={kind} size={16} />
          {KIND_LABEL[kind]}
        </label>
      ))}
    </fieldset>
  );
}

function DraftForm({
  request,
  onClose,
  onStartRun,
  onReloadRevise,
}: Omit<Props, 'onCreated'> & { request: NewArtifactRequest }) {
  const app = useApp();
  const [target, setTarget] = useState(request.revise ?? null);
  const [kind, setKind] = useState<NativeArtifactKind>(request.revise?.kind ?? request.kind);
  const [prompt, setPrompt] = useState(request.prompt ?? '');
  const [grounded, setGrounded] = useState(true);
  const [planned, setPlanned] = useState(false);
  const [title, setTitle] = useState(request.revise?.title ?? 'Untitled draft');
  const [sections, setSections] = useState<DocSectionDraft[]>([DEFAULT_SECTION]);
  const [documentBlockQuota, setDocumentBlockQuota] = useState(6);
  const [sheets, setSheets] = useState<SheetDraft[]>([DEFAULT_SHEET]);
  const [cellQuota, setCellQuota] = useState(39);
  const [unitTokens, setUnitTokens] = useState(1200);
  const [unitRepairs, setUnitRepairs] = useState(1);
  const [checkpointBytes, setCheckpointBytes] = useState(4000000);
  const [limits, setLimits] = useState<ArtifactRunLimits | null>(null);
  const [limitsError, setLimitsError] = useState<string | null>(null);
  const { busy, error, run } = useAsyncAction();
  const [conflict, setConflict] = useState(false);
  const runKey = useIdempotencyKey();
  const scope = request.scope !== undefined ? request.scope : app.scope;
  const plannable = planned && kind !== 'html';

  // The server's current allowance is needed to validate a plan; load it when planning is switched on.
  useEffect(() => {
    setLimits(null);
    setLimitsError(null);
    if (!plannable) return;
    let alive = true;
    getClient()
      .getArtifactRunLimits()
      .then((loaded) => alive && setLimits(loaded))
      .catch((cause: unknown) => alive && setLimitsError(errorMessage(cause)));
    return () => {
      alive = false;
    };
  }, [plannable]);

  const submit = () =>
    run(async () => {
      const sourceIds = grounded ? scope : null;
      if (grounded && sourceIds && sourceIds.length === 0)
        throw new Error('No sources are selected. Pick some under “Search in”, or turn off “Use my sources”.');
      const outputPlan = plannable
        ? buildOutputPlan(
            {
              kind,
              title,
              sections,
              documentBlockQuota,
              sheets,
              cellQuota,
              unitTokens,
              unitRepairs,
              checkpointBytes,
            },
            limits,
            limitsError,
          )
        : undefined;
      const inputs: ArtifactRunRequest['inputs'] = {
        prompt,
        sourceIds,
        mode: grounded ? 'grounded' : 'authoring',
        ...(outputPlan ? { outputPlan } : {}),
        ...(target ? { artifactId: target.artifactId, expectedRevisionId: target.revisionId } : {}),
      };
      const label = outputPlan ? title : prompt.trim().replace(/\s+/g, ' ').slice(0, 80);
      try {
        await onStartRun({
          kind,
          inputs,
          idempotencyKey: runKey.keyFor({ kind, inputs }),
          label: target ? `Changes to ${target.title}` : label,
          revises: target ? { artifactId: target.artifactId, title: target.title } : undefined,
        });
      } catch (cause) {
        if (statusOf(cause) === 409) setConflict(true);
        throw cause;
      }
      runKey.reset();
      onClose();
    });

  const reload = async () => {
    if (!target) return;
    await run(async () => {
      setTarget(await onReloadRevise(target.artifactId));
      setConflict(false);
    });
  };

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {target ? (
        <p className="muted">
          Revising <strong>{target.title}</strong>. The result is saved as a new version; the current one is kept.
        </p>
      ) : (
        <KindPicker value={kind} onChange={(next) => setKind(next)} disabled={busy} />
      )}
      <label className="field">
        <span>{target ? 'What should change?' : 'What should it contain?'}</span>
        <textarea
          rows={4}
          maxLength={15000}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={
            kind === 'workbook'
              ? 'e.g. A four-year course plan for the Chemistry BS, one row per course'
              : 'e.g. A one-page checklist of requirements for the Neuroscience major'
          }
        />
      </label>
      <label className="toggle-row">
        <input type="checkbox" checked={grounded} onChange={(e) => setGrounded(e.target.checked)} />
        <span>
          <strong>Use my sources</strong>
          <small>
            {grounded
              ? `Grounded in ${scopeLabel(scope).toLowerCase()}, with each claim tied to a passage.`
              : 'Written from your instructions only — not checked against your sources.'}
          </small>
        </span>
      </label>
      {kind !== 'html' && (
        <details className="advanced">
          <summary>Advanced: plan it section by section</summary>
          <label className="toggle-row">
            <input type="checkbox" checked={planned} onChange={(e) => setPlanned(e.target.checked)} />
            <span>
              <strong>Plan a document or workbook in sections</strong>
              <small>Set the parts, their sizes, and generation limits yourself.</small>
            </span>
          </label>
          {planned && (
            <div className="stack">
              <label className="field">
                <span>Generated artifact title</span>
                <input value={title} onChange={(e) => setTitle(e.target.value)} />
              </label>
              {kind === 'document' ? (
                <DocumentPlanEditor
                  sections={sections}
                  onSectionsChange={setSections}
                  blockQuota={documentBlockQuota}
                  onBlockQuotaChange={setDocumentBlockQuota}
                />
              ) : (
                <WorkbookPlanEditor
                  sheets={sheets}
                  onSheetsChange={setSheets}
                  cellQuota={cellQuota}
                  onCellQuotaChange={setCellQuota}
                />
              )}
              <PlanLimitsEditor
                unitTokens={unitTokens}
                onUnitTokensChange={setUnitTokens}
                unitRepairs={unitRepairs}
                onUnitRepairsChange={setUnitRepairs}
                checkpointBytes={checkpointBytes}
                onCheckpointBytesChange={setCheckpointBytes}
                allowance={limits}
                allowanceError={limitsError}
              />
            </div>
          )}
        </details>
      )}
      {conflict && target && (
        <div className="callout callout-error" role="alert">
          <strong>{target.title} changed since you opened it.</strong>
          <p>Your request is still here. Load the latest version, check it, then send your request again.</p>
          <button type="button" className="secondary-button" onClick={() => void reload()} disabled={busy}>
            Load latest version
          </button>
        </div>
      )}
      {error && !conflict && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="secondary-button" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="primary-button" disabled={busy || !prompt.trim()}>
          <SparkIcon size={16} />
          {busy ? 'Starting…' : target ? 'Make these changes' : `Create ${KIND_LABEL[kind].toLowerCase()}`}
        </button>
      </div>
    </form>
  );
}

function BlankForm({
  request,
  onClose,
  onCreated,
}: Pick<Props, 'onClose' | 'onCreated'> & { request: NewArtifactRequest }) {
  const [kind, setKind] = useState<NativeArtifactKind>(request.kind);
  const [title, setTitle] = useState(BLANK_TITLES[request.kind]);
  const [titleEdited, setTitleEdited] = useState(false);
  const [markdown, setMarkdown] = useState('');
  const [html, setHtml] = useState('<h1>My page</h1>\n<p>Start writing here.</p>');
  const { busy, error, run } = useAsyncAction();

  const create = () =>
    run(async () => {
      let spec: NativeArtifactSpec;
      if (kind === 'document') spec = parseMarkdown(title, markdown);
      else if (kind === 'html') spec = { kind: 'html', title: title || BLANK_TITLES.html, html, provenance: {} };
      else spec = emptyWorkbook(title || BLANK_TITLES.workbook);
      await onCreated(await getClient().createArtifact(spec));
      onClose();
    });

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        void create();
      }}
    >
      <KindPicker
        value={kind}
        disabled={busy}
        onChange={(next) => {
          setKind(next);
          if (!titleEdited) setTitle(BLANK_TITLES[next]);
        }}
      />
      <label className="field">
        <span>Title</span>
        <input
          value={title}
          maxLength={500}
          onChange={(e) => {
            setTitle(e.target.value);
            setTitleEdited(true);
          }}
        />
      </label>
      {kind === 'document' && (
        <label className="field">
          <span>Content (Markdown)</span>
          <textarea
            rows={10}
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            placeholder={'# Heading\n\nWrite your document here…'}
          />
        </label>
      )}
      {kind === 'html' && (
        <label className="field">
          <span>HTML content</span>
          <textarea className="code" rows={10} value={html} onChange={(e) => setHtml(e.target.value)} />
        </label>
      )}
      {kind === 'workbook' && <p className="muted">Starts with an empty sheet. Edit cells after it’s created.</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="secondary-button" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="primary-button" disabled={busy}>
          {busy ? 'Creating…' : 'Create'}
        </button>
      </div>
    </form>
  );
}

function ImportForm({ onClose, onCreated }: Pick<Props, 'onClose' | 'onCreated'>) {
  const { busy, error, run } = useAsyncAction();
  const fileRef = useRef<HTMLInputElement>(null);

  const importFile = async (file?: File) => {
    if (!file) return;
    await run(async () => {
      const result = await getClient().importArtifact(file);
      await onCreated(
        result,
        result.original?.preserved ? `Imported ${file.name}. The original file is kept too.` : `Imported ${file.name}.`,
      );
      onClose();
    });
  };

  return (
    <div className="stack">
      <button type="button" className="dropzone" onClick={() => fileRef.current?.click()} disabled={busy}>
        <UploadIcon size={22} />
        <strong>{busy ? 'Importing…' : 'Choose a file'}</strong>
        <small>Word (.docx), Excel (.xlsx), or Markdown (.md). The original is kept alongside your edits.</small>
      </button>
      <input
        ref={fileRef}
        className="visually-hidden"
        type="file"
        accept=".md,.markdown,.docx,.xlsx"
        aria-label="Choose a Markdown, DOCX, or XLSX file"
        onChange={(e) => {
          void importFile(e.currentTarget.files?.[0]);
          e.currentTarget.value = '';
        }}
      />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

const TAB_LABELS: Record<NewArtifactTab, string> = {
  draft: 'Draft it for me',
  blank: 'Start blank',
  import: 'Import a file',
};

function NewArtifactForm(props: Props & { request: NewArtifactRequest }) {
  const [tab, setTab] = useState<NewArtifactTab>(props.request.revise ? 'draft' : props.request.tab);
  return (
    <>
      {!props.request.revise && (
        <div className="tabs" role="tablist" aria-label="How to start">
          {(Object.keys(TAB_LABELS) as NewArtifactTab[]).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={tab === key ? 'tab tab-on' : 'tab'}
              onClick={() => setTab(key)}
            >
              {TAB_LABELS[key]}
            </button>
          ))}
        </div>
      )}
      {tab === 'draft' && <DraftForm {...props} />}
      {tab === 'blank' && <BlankForm request={props.request} onClose={props.onClose} onCreated={props.onCreated} />}
      {tab === 'import' && <ImportForm onClose={props.onClose} onCreated={props.onCreated} />}
    </>
  );
}

export function NewArtifactDialog(props: Props) {
  const { request } = props;
  return (
    <Dialog
      open={request !== null}
      title={request?.revise ? 'Ask for changes' : 'Make something'}
      description={
        request?.revise ? undefined : 'Documents, spreadsheets, and web pages you make here stay with this chat.'
      }
      onClose={props.onClose}
      size="wide"
    >
      {request && <NewArtifactForm key={request.nonce} {...props} request={request} />}
    </Dialog>
  );
}
