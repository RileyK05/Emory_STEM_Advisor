import { useEffect, useRef } from 'react';
import { AppProvider, useApp } from './state/AppContext';
import { Header } from './components/Header';
import { ChatMessageView } from './components/ChatMessage';
import { ChatInput } from './components/ChatInput';
import { SourcePanel } from './components/SourcePanel';

function Chat() {
  const { messages } = useApp();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <main className="chat-column">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <h2>Ask a question about your program documents</h2>
            <p>Every answer is grounded in the uploaded sources and cites exactly where it came from.</p>
          </div>
        )}
        {messages.map((m) => (
          <ChatMessageView key={m.id} message={m} />
        ))}
        <div ref={bottomRef} />
      </div>
      <ChatInput />
    </main>
  );
}

export default function App() {
  return (
    <AppProvider>
      <div className="app">
        <Header />
        <div className="app-body">
          <Chat />
          <SourcePanel />
        </div>
      </div>
    </AppProvider>
  );
}
