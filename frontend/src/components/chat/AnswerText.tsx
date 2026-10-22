import { memo } from 'react';
import Markdown from 'react-markdown';
import type { Components } from 'react-markdown';
import type { ContextItem } from '../../api/types';

const MARKER = /\[(\d+(?:\s*[-–,;]\s*\d+)*)\](?!\()/g;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const INLINE_CODE = /(`+)[\s\S]*?\1/g;
const CITE_PREFIX = '#cite-';

/** The passage numbers a marker body such as `1`, `1, 3` or `2-4` names, or null if it is not one. */
function markerNumbers(body: string, count: number): number[] | null {
  const numbers: number[] = [];
  for (const part of body.split(/[,;]/)) {
    const match = /^\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*$/.exec(part);
    if (!match) return null;
    const start = Number(match[1]);
    const end = match[2] ? Number(match[2]) : start;
    if (start < 1 || end < start || end > count || end - start > 20) return null;
    for (let n = start; n <= end; n += 1) numbers.push(n);
  }
  return numbers;
}

function linkMarkers(text: string, count: number) {
  return text.replace(MARKER, (whole, body: string) => {
    const numbers = markerNumbers(body, count);
    return numbers ? numbers.map((n) => `[${n}](${CITE_PREFIX}${n})`).join('') : whole;
  });
}

/** Turn numbered citation markers outside code into links the renderer shows as footnote buttons. */
function withCitationLinks(markdown: string, count: number): string {
  if (count === 0) return markdown;
  let fence: string | null = null;
  return markdown
    .split('\n')
    .map((line) => {
      const opening = FENCE.exec(line);
      if (fence) {
        if (line.trim().startsWith(fence)) fence = null;
        return line;
      }
      if (opening) {
        fence = opening[1];
        return line;
      }
      let out = '';
      let last = 0;
      for (const code of line.matchAll(INLINE_CODE)) {
        out += linkMarkers(line.slice(last, code.index), count) + code[0];
        last = (code.index ?? 0) + code[0].length;
      }
      return out + linkMarkers(line.slice(last), count);
    })
    .join('\n');
}

interface Props {
  text: string;
  context: ContextItem[];
  onCite: (passageNumber: number) => void;
}

export const AnswerText = memo(function AnswerText({ text, context, onCite }: Props) {
  const components: Components = {
    a({ href, children }) {
      if (href?.startsWith(CITE_PREFIX)) {
        const n = Number(href.slice(CITE_PREFIX.length));
        const passage = context[n - 1];
        return (
          <button
            type="button"
            className="cite-mark"
            aria-label={`Source ${n}: ${passage?.sourceName ?? ''} ${passage?.locatorLabel ?? ''}`.trim()}
            title={passage ? `${passage.sourceName} · ${passage.locatorLabel}` : undefined}
            onClick={() => onCite(n)}
          >
            {n}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer noopener">
          {children}
        </a>
      );
    },
  };
  return (
    <div className="prose">
      <Markdown components={components}>{withCitationLinks(text, context.length)}</Markdown>
    </div>
  );
});
