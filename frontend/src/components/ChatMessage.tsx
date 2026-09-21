import Markdown from 'react-markdown';
import type { ChatMessage } from '../state/AppContext';
import { CitationCard } from './CitationCard';
import { DebugPanel } from './DebugPanel';

export function ChatMessageView({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="message message-user">
        <div className="message-body">{message.text}</div>
      </div>
    );
  }

  return (
    <div className="message message-assistant">
      <div className="message-body">
        {message.status === 'sending' && (
          <div className="message-loading" aria-label="Advisor is thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </div>
        )}
        {message.status === 'error' && (
          <div className="message-error">
            <strong>Something went wrong.</strong> {message.errorText}
          </div>
        )}
        {message.status === 'done' && message.refusalReason && (
          <div className="message-refusal">
            <span className="refusal-icon" aria-hidden>
              ∅
            </span>
            <div>
              <strong>No grounded answer.</strong>
              <p>{message.refusalReason}</p>
            </div>
          </div>
        )}
        {message.status === 'done' && message.text && (
          <>
            <div className="message-markdown">
              <Markdown>{message.text}</Markdown>
            </div>
            {message.citations && message.citations.length > 0 && (
              <div className="citation-row">
                {message.citations.map((c) => (
                  <CitationCard key={`${c.chunkId}`} citation={c} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
      {message.trace && <DebugPanel trace={message.trace} />}
    </div>
  );
}
