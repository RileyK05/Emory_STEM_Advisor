import type { DocumentBlock, DocumentSpec } from '../../api/types';

/** The draft with `patch` applied to the block `blockId`. */
export function withBlockPatch(draft: DocumentSpec, blockId: string, patch: Partial<DocumentBlock>): DocumentSpec {
  return { ...draft, blocks: draft.blocks.map((block) => (block.id === blockId ? { ...block, ...patch } : block)) };
}

function parseIdList(value: unknown): string[] {
  if (typeof value !== 'string') return [];
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? (parsed as string[]) : [];
  } catch {
    return []; // older free-form provenance
  }
}

/** Ids a previous revision already marked unverified (their evidence was removed). */
export function unverifiedBlockIds(spec: DocumentSpec): Set<string> {
  return new Set(parseIdList(spec.provenance?.unverifiedElements));
}

/**
 * Compare an edited draft with the saved revision. Blocks that are new or whose content
 * changed lose their source evidence and are recorded as unverified; everything else is kept.
 */
export function editedDocument(saved: DocumentSpec, draft: DocumentSpec) {
  const savedById = new Map<string, DocumentBlock>();
  for (const block of saved.blocks) if (!savedById.has(block.id)) savedById.set(block.id, block);
  const changed = new Set(
    draft.blocks
      .filter((block) => {
        const previous = savedById.get(block.id);
        return !previous || JSON.stringify(previous) !== JSON.stringify(block);
      })
      .map((block) => block.id),
  );
  const evidence = Object.fromEntries(Object.entries(draft.evidence ?? {}).filter(([id]) => !changed.has(id)));
  const prior = parseIdList(draft.provenance?.unverifiedElements);
  const provenance = {
    ...(draft.provenance ?? {}),
    ...(changed.size || prior.length
      ? { unverifiedElements: JSON.stringify([...new Set([...prior, ...changed])]) }
      : {}),
  };
  return { spec: { ...draft, evidence, provenance }, changed };
}
