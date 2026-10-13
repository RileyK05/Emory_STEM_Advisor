import type { NativeArtifactKind } from '../../api/types';

export type BatchMode = 'grounded' | 'authoring';

interface Props {
  busy: boolean;
  /** This exact request was already sent; any edit starts a new batch intent. */
  attempted: boolean;
  kind: NativeArtifactKind;
  onKindChange: (kind: NativeArtifactKind) => void;
  mode: BatchMode;
  onModeChange: (mode: BatchMode) => void;
  /** How the current source selection scopes grounded requests. */
  sourceScope: { enabled: boolean; selectedCount: number };
  prompts: string[];
  onPromptsChange: (prompts: string[]) => void;
  onSubmit: () => void;
}

export const MAX_PROMPTS = 50;

export function NewBatchForm(props: Props) {
  const { busy, attempted, kind, mode, prompts, sourceScope } = props;
  return (
    <fieldset disabled={busy}>
      <legend>New batch</legend>
      <label>
        Batch artifact type
        <select
          aria-label="Batch artifact type"
          value={kind}
          onChange={(event) => props.onKindChange(event.target.value as NativeArtifactKind)}
        >
          <option value="document">Document</option>
          <option value="workbook">Workbook</option>
          <option value="html">HTML</option>
        </select>
      </label>
      <label>
        Batch request mode
        <select
          aria-label="Batch request mode"
          value={mode}
          onChange={(event) => props.onModeChange(event.target.value as BatchMode)}
        >
          <option value="grounded">Source-grounded</option>
          <option value="authoring">Authoring drafts</option>
        </select>
      </label>
      <p>
        {mode === 'authoring'
          ? 'Authoring requests use no source passages.'
          : sourceScope.enabled
            ? `${sourceScope.selectedCount} selected source(s) will be used, including an explicit empty selection.`
            : 'All sources in the current collection will be used.'}
      </p>
      {prompts.map((prompt, index) => (
        <label key={index}>
          Prompt {index + 1}
          <textarea
            aria-label={`Batch prompt ${index + 1}`}
            rows={2}
            maxLength={15000}
            value={prompt}
            onChange={(event) =>
              props.onPromptsChange(prompts.map((item, itemIndex) => (itemIndex === index ? event.target.value : item)))
            }
          />
        </label>
      ))}
      <div className="artifact-actions">
        <button
          type="button"
          onClick={() => props.onPromptsChange([...prompts, ''])}
          disabled={prompts.length >= MAX_PROMPTS}
        >
          Add prompt
        </button>
        {prompts.length > 1 && (
          <button type="button" onClick={() => props.onPromptsChange(prompts.slice(0, -1))}>
            Remove last prompt
          </button>
        )}
        <button
          type="button"
          onClick={props.onSubmit}
          disabled={busy || attempted || prompts.some((prompt) => !prompt.trim())}
        >
          Submit batch
        </button>
      </div>
      {attempted && (
        <p role="status">
          This request was already submitted or attempted. Edit a prompt or request setting to create a new batch
          intent; inspect the existing batch above before deciding.
        </p>
      )}
    </fieldset>
  );
}
