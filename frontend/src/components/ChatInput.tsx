import { useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useApp } from '../state/AppContext';

export function ChatInput() {
  const { sendQuery, sending } = useApp();
  const [text, setText] = useState('');

  const submit = () => {
    if (!text.trim() || sending) return;
    sendQuery(text);
    setText('');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="chat-input">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about your program documents… (Enter to send, Shift+Enter for a new line)"
        rows={2}
        disabled={sending}
      />
      <button type="button" className="send-button" onClick={submit} disabled={sending || !text.trim()}>
        {sending ? 'Sending…' : 'Send'}
      </button>
    </div>
  );
}
