import { useEffect, useRef, useState } from 'react';
import { useApp } from '../state/AppContext';

export function SourcePanel() {
  const { sources, highlight, sendQuery } = useApp();
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const [uploadNote, setUploadNote] = useState<string | null>(null);

  useEffect(() => {
    if (!highlight) return;
    const el = itemRefs.current.get(highlight.sourceId);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlight]);

  const onUploadClick = () => {
    setUploadNote('Upload requires the backend ingest pipeline, which is not connected yet.');
  };

  return (
    <aside className="source-panel">
      <div className="source-panel-header">
        <h2>Sources</h2>
        <button type="button" className="upload-button" onClick={onUploadClick}>
          + Upload
        </button>
      </div>
      {uploadNote && <p className="upload-note">{uploadNote}</p>}
      {sources.length === 0 && <p className="source-empty">No sources loaded.</p>}
      <ul className="source-list">
        {sources.map((s) => (
          <li
            key={s.id}
            ref={(el) => {
              if (el) itemRefs.current.set(s.id, el);
              else itemRefs.current.delete(s.id);
            }}
            className={`source-item ${highlight?.sourceId === s.id ? 'source-item-highlight' : ''}`}
          >
            <div className="source-item-name">{s.name}</div>
            <div className="source-item-meta">
              <span className={`source-status source-status-${s.status}`}>{s.status}</span>
              <span>{s.locatorType}s</span>
              {s.chunkCount > 0 && <span>{s.chunkCount} chunks</span>}
            </div>
          </li>
        ))}
      </ul>
      <div className="source-panel-hint">
        <p>Demo queries to try:</p>
        <ul>
          <li>
            <button type="button" onClick={() => sendQuery('What is a vector basis?')}>
              "What is a vector basis?"
            </button>
          </li>
          <li>
            <button type="button" onClick={() => sendQuery('How do embeddings find related material?')}>
              embedding-heavy answer
            </button>
          </li>
          <li>
            <button type="button" onClick={() => sendQuery('What are the prerequisites for this topic?')}>
              graph seam + warnings
            </button>
          </li>
          <li>
            <button type="button" onClick={() => sendQuery('How does quantum entanglement work?')}>
              refusal
            </button>
          </li>
        </ul>
      </div>
    </aside>
  );
}
