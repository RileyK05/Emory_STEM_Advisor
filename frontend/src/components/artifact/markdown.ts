import type { DocumentBlock, DocumentSpec } from '../../api/types';
import { makeId } from './ids';

/** Split one Markdown table row into trimmed cells, honouring `\|` escapes. */
function splitRow(line: string): string[] {
  const body = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  const cells: string[] = [];
  let cell = '';
  let escaped = false;
  for (const char of body) {
    if (char === '|' && !escaped) {
      cells.push(cell.trim());
      cell = '';
    } else {
      cell += char;
    }
    if (char === '\\' && !escaped) escaped = true;
    else escaped = false;
  }
  cells.push(cell.trim());
  return cells.map((item) => item.replace(/\\\|/g, '|'));
}

const LIST_ITEM = /^\s*(?:[-*+]\s+|\d+\.\s+)/;

/** Parse Markdown into the native document block model (headings, lists, quotes, code, tables, paragraphs). */
export function parseMarkdown(title: string, source: string): DocumentSpec {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: DocumentBlock[] = [];
  for (let i = 0; i < lines.length; ) {
    if (!lines[i].trim()) {
      i++;
      continue;
    }
    const fence = /^\s*(`{3,}|~{3,})(.*)$/.exec(lines[i]);
    if (fence) {
      const marker = fence[1][0];
      const length = fence[1].length;
      const code: string[] = [];
      i++;
      while (i < lines.length && !new RegExp('^\\s*' + marker + '{' + length + ',}\\s*$').test(lines[i]))
        code.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({ id: makeId('block'), type: 'code', language: fence[2].trim() || null, text: code.join('\n') });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(lines[i]);
    if (heading) {
      blocks.push({ id: makeId('block'), type: 'heading', level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    if (/^\s*>/.test(lines[i])) {
      const quoted: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s*> ?/, ''));
      blocks.push({ id: makeId('block'), type: 'quote', text: quoted.join('\n') });
      continue;
    }
    if (LIST_ITEM.test(lines[i])) {
      const ordered = /^\s*(\d+)\./.test(lines[i]);
      const items: string[] = [];
      while (i < lines.length && LIST_ITEM.test(lines[i])) items.push(lines[i++].replace(LIST_ITEM, ''));
      blocks.push({ id: makeId('block'), type: 'list', ordered, items });
      continue;
    }
    if (lines[i].trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const headers = splitRow(lines[i]);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(splitRow(lines[i++]));
      blocks.push({ id: makeId('block'), type: 'table', headers, rows });
      continue;
    }
    const paragraph: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6})\s/.test(lines[i]) &&
      !/^\s*(`{3,}|~{3,})/.test(lines[i]) &&
      !/^\s*>/.test(lines[i]) &&
      !LIST_ITEM.test(lines[i])
    )
      paragraph.push(lines[i++]);
    blocks.push({ id: makeId('block'), type: 'paragraph', text: paragraph.join('\n') });
  }
  return { kind: 'document', title: title || 'Untitled document', blocks, evidence: {}, provenance: {} };
}
