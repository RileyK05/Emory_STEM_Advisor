import type { DocumentBlock, DocumentSpec } from '../../api/types';

const BLOCK_LABELS: Record<DocumentBlock['type'], string> = {
  paragraph: 'Paragraph',
  heading: 'Heading',
  list: 'List',
  table: 'Table',
  code: 'Code',
  quote: 'Quote',
};

interface Props {
  draft: DocumentSpec;
  /** Blocks edited since the saved revision (their source evidence will be dropped on save). */
  changed: Set<string>;
  /** Blocks a previous revision already marked unverified. */
  unverified: Set<string>;
  disabled?: boolean;
  onBlockChange: (blockId: string, patch: Partial<DocumentBlock>) => void;
}

function BlockText({
  block,
  disabled,
  onChange,
}: {
  block: DocumentBlock;
  disabled?: boolean;
  onChange: (patch: Partial<DocumentBlock>) => void;
}) {
  return (
    <textarea
      aria-label={`Block ${block.id}`}
      value={block.text ?? ''}
      disabled={disabled}
      onChange={(e) => onChange({ text: e.target.value })}
      rows={Math.max(1, Math.min(12, (block.text ?? '').split('\n').length))}
    />
  );
}

function BlockFields({
  block,
  index,
  disabled,
  onChange,
}: {
  block: DocumentBlock;
  index: number;
  disabled?: boolean;
  onChange: (patch: Partial<DocumentBlock>) => void;
}) {
  switch (block.type) {
    case 'heading':
      return (
        <>
          <label>
            Heading level{' '}
            <select
              aria-label={`Heading level ${index + 1}`}
              value={block.level ?? 1}
              disabled={disabled}
              onChange={(e) => onChange({ level: Number(e.target.value) })}
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <BlockText block={block} disabled={disabled} onChange={onChange} />
        </>
      );
    case 'paragraph':
    case 'quote':
    case 'code':
      return (
        <>
          <BlockText block={block} disabled={disabled} onChange={onChange} />
          {block.type === 'code' && (
            <label>
              Code language{' '}
              <input
                aria-label={`Language ${block.id}`}
                value={block.language ?? ''}
                disabled={disabled}
                onChange={(e) => onChange({ language: e.target.value || null })}
              />
            </label>
          )}
        </>
      );
    case 'list':
      return (
        <>
          <label>
            Ordered list{' '}
            <input
              type="checkbox"
              checked={Boolean(block.ordered)}
              disabled={disabled}
              onChange={(e) => onChange({ ordered: e.target.checked })}
            />
          </label>
          <textarea
            aria-label={`Block ${block.id}`}
            value={(block.items ?? []).join('\n')}
            disabled={disabled}
            onChange={(e) => onChange({ items: e.target.value.split('\n') })}
          />
        </>
      );
    case 'table':
      return (
        <>
          <label>
            Headers (one per line)
            <textarea
              aria-label={`Headers ${block.id}`}
              value={(block.headers ?? []).join('\n')}
              disabled={disabled}
              onChange={(e) => onChange({ headers: e.target.value.split('\n') })}
            />
          </label>
          <label>
            Rows (tab-separated cells, one row per line)
            <textarea
              aria-label={`Rows ${block.id}`}
              value={(block.rows ?? []).map((row) => row.join('\t')).join('\n')}
              disabled={disabled}
              onChange={(e) => onChange({ rows: e.target.value.split('\n').map((line) => line.split('\t')) })}
            />
          </label>
        </>
      );
    default:
      return null;
  }
}

export function DocumentEditor({ draft, changed, unverified, disabled, onBlockChange }: Props) {
  return (
    <section className="document-editor" aria-label="Document editor">
      {draft.provenance?.authority === 'generated_draft' && (
        <p className="callout callout-info" role="status">
          Drafted from your instructions — not checked against your sources.
        </p>
      )}
      {draft.blocks.map((block, index) => (
        <fieldset
          key={block.id}
          data-block-id={block.id}
          className={
            `doc-block doc-block-${block.type}` + (block.type === 'heading' ? ` doc-h${block.level ?? 1}` : '')
          }
        >
          <legend className="doc-block-type">{BLOCK_LABELS[block.type] ?? block.type}</legend>
          <BlockFields
            block={block}
            index={index}
            disabled={disabled}
            onChange={(patch) => onBlockChange(block.id, patch)}
          />
          <div className="doc-block-meta">
            {changed.has(block.id) ? (
              <small className="tag tag-warn" role="status">
                Edited · unverified — its source link will be removed when you save
              </small>
            ) : unverified.has(block.id) ? (
              <small className="tag tag-warn" role="status">
                Edited · unverified — no longer tied to a source
              </small>
            ) : (
              (draft.evidence?.[block.id] ?? []).map((ref) => (
                <small key={ref.chunk_id} className="tag" title={ref.text_snapshot}>
                  Source: {ref.source_name} · {ref.locator_label}
                </small>
              ))
            )}
          </div>
        </fieldset>
      ))}
      {draft.blocks.length === 0 && <p className="muted">This document is empty.</p>}
    </section>
  );
}
