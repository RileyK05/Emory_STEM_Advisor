import type { Dispatch, SetStateAction } from 'react';
import type { ArtifactRunLimits } from '../../api/types';
import { MAX_UNITS, newSection, newSheet } from './outputPlan';
import type { DocSectionDraft, SheetDraft } from './outputPlan';

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      {label}{' '}
      <input
        type="number"
        min={String(min)}
        max={String(max)}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

/** Replace the item at `index` with `{ ...item, ...patch }`. */
function patchAt<T>(index: number, patch: Partial<T>) {
  return (all: T[]) => all.map((item, i) => (i === index ? { ...item, ...patch } : item));
}

interface DocumentPlanProps {
  sections: DocSectionDraft[];
  onSectionsChange: Dispatch<SetStateAction<DocSectionDraft[]>>;
  blockQuota: number;
  onBlockQuotaChange: (value: number) => void;
}

export function DocumentPlanEditor({ sections, onSectionsChange, blockQuota, onBlockQuotaChange }: DocumentPlanProps) {
  const update = (index: number, patch: Partial<DocSectionDraft>) => onSectionsChange(patchAt(index, patch));
  return (
    <fieldset>
      <legend>Document plan</legend>
      {sections.map((section, index) => (
        <fieldset key={section.id}>
          <legend>Section {index + 1}</legend>
          <label>
            Section title <input value={section.title} onChange={(e) => update(index, { title: e.target.value })} />
          </label>
          <NumberField
            label="Target words"
            value={section.targetWords}
            min={1}
            max={20000}
            onChange={(targetWords) => update(index, { targetWords })}
          />
          <NumberField
            label="Minimum words"
            value={section.minWords}
            min={1}
            max={20000}
            onChange={(minWords) => update(index, { minWords })}
          />
          <NumberField
            label="Maximum words"
            value={section.maxWords}
            min={1}
            max={20000}
            onChange={(maxWords) => update(index, { maxWords })}
          />
          <NumberField
            label="Target content blocks"
            value={section.targetBlocks}
            min={1}
            max={64}
            onChange={(targetBlocks) => update(index, { targetBlocks })}
          />
          <NumberField
            label="Minimum content blocks"
            value={section.minBlocks}
            min={1}
            max={64}
            onChange={(minBlocks) => update(index, { minBlocks })}
          />
          <NumberField
            label="Maximum content blocks"
            value={section.maxBlocks}
            min={1}
            max={64}
            onChange={(maxBlocks) => update(index, { maxBlocks })}
          />
          {sections.length > 1 && (
            <button type="button" onClick={() => onSectionsChange((all) => all.filter((_, i) => i !== index))}>
              Remove section {index + 1}
            </button>
          )}
        </fieldset>
      ))}
      {sections.length < MAX_UNITS && (
        <button type="button" onClick={() => onSectionsChange((all) => [...all, newSection(all.length)])}>
          Add section
        </button>
      )}
      <NumberField
        label="Total document blocks, including headings"
        value={blockQuota}
        min={1}
        max={1000}
        onChange={onBlockQuotaChange}
      />
    </fieldset>
  );
}

interface WorkbookPlanProps {
  sheets: SheetDraft[];
  onSheetsChange: Dispatch<SetStateAction<SheetDraft[]>>;
  cellQuota: number;
  onCellQuotaChange: (value: number) => void;
}

export function WorkbookPlanEditor({ sheets, onSheetsChange, cellQuota, onCellQuotaChange }: WorkbookPlanProps) {
  const update = (index: number, patch: Partial<SheetDraft>) => onSheetsChange(patchAt(index, patch));
  return (
    <fieldset>
      <legend>Workbook plan</legend>
      {sheets.map((sheet, index) => (
        <fieldset key={sheet.id}>
          <legend>Worksheet {index + 1}</legend>
          <label>
            Worksheet name <input value={sheet.name} onChange={(e) => update(index, { name: e.target.value })} />
          </label>
          <label>
            Columns (one per line: key|label|type){' '}
            <textarea
              rows={4}
              value={sheet.columnText}
              onChange={(e) => update(index, { columnText: e.target.value })}
            />
            <small>Types: text, integer, number, date, formula. Formula columns contain local formulas.</small>
          </label>
          <NumberField
            label="Target rows"
            value={sheet.targetRows}
            min={1}
            max={100000}
            onChange={(targetRows) => update(index, { targetRows })}
          />
          <NumberField
            label="Minimum rows"
            value={sheet.minRows}
            min={1}
            max={100000}
            onChange={(minRows) => update(index, { minRows })}
          />
          <NumberField
            label="Maximum rows"
            value={sheet.maxRows}
            min={1}
            max={100000}
            onChange={(maxRows) => update(index, { maxRows })}
          />
          {sheets.length > 1 && (
            <button type="button" onClick={() => onSheetsChange((all) => all.filter((_, i) => i !== index))}>
              Remove worksheet {index + 1}
            </button>
          )}
        </fieldset>
      ))}
      {sheets.length < MAX_UNITS && (
        <button type="button" onClick={() => onSheetsChange((all) => [...all, newSheet(all.length)])}>
          Add worksheet
        </button>
      )}
      <NumberField
        label="Total workbook cells, including headers"
        value={cellQuota}
        min={1}
        max={100000}
        onChange={onCellQuotaChange}
      />
    </fieldset>
  );
}

interface LimitsProps {
  unitTokens: number;
  onUnitTokensChange: (value: number) => void;
  unitRepairs: number;
  onUnitRepairsChange: (value: number) => void;
  checkpointBytes: number;
  onCheckpointBytesChange: (value: number) => void;
  /** The server's current allowance, once loaded. */
  allowance: ArtifactRunLimits | null;
  allowanceError: string | null;
}

export function PlanLimitsEditor(props: LimitsProps) {
  const { allowance, allowanceError } = props;
  return (
    <fieldset>
      <legend>Generation limits</legend>
      <NumberField
        label="Maximum output tokens per section or sheet"
        value={props.unitTokens}
        min={64}
        max={8192}
        onChange={props.onUnitTokensChange}
      />
      <NumberField
        label="Repair attempts per section or sheet"
        value={props.unitRepairs}
        min={0}
        max={2}
        onChange={props.onUnitRepairsChange}
      />
      <NumberField
        label="Maximum saved checkpoint size in bytes"
        value={props.checkpointBytes}
        min={1024}
        max={16000000}
        onChange={props.onCheckpointBytesChange}
      />
      {allowance ? (
        <p role="status">
          Current allowance: {allowance.maxCalls} model calls and {allowance.totalTokenBudget} output tokens total;{' '}
          {allowance.perCallOutputCap} output tokens per section or sheet. Prompt/context use also counts toward
          completion and may reduce what fits.
        </p>
      ) : (
        <p role={allowanceError ? 'alert' : 'status'}>
          {allowanceError ? `Could not load generation limits: ${allowanceError}` : 'Loading generation limits…'}
        </p>
      )}
    </fieldset>
  );
}
