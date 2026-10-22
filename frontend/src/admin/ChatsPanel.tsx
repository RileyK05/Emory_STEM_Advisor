import { useState } from 'react';
import type { AdminConversation, AdminTranscript } from '../api/types';
import { deleteAdminConversation, getAdminTranscript, listAdminConversations } from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
import {
  AdminError,
  AdminSearch,
  AdminWorkspace,
  DetailHead,
  FieldLine,
  PAGE,
  Pager,
  RefreshButton,
  usePagedList,
  type AdminPanelProps,
} from './adminUi';

export function ChatsPanel({ toolbar }: AdminPanelProps) {
  const [search, setSearch] = useState('');
  const list = usePagedList((offset) => listAdminConversations({ query: search, limit: PAGE, offset }), [search]);
  const [transcript, setTranscript] = useState<AdminTranscript | null>(null);

  const openTranscript = (conversation: AdminConversation) =>
    list.run(async () => setTranscript(await getAdminTranscript(conversation.conversationId)));

  const deleteConversation = async (conversation: AdminConversation) => {
    if (!window.confirm(`Delete “${conversation.title}” and its turn jobs? This cannot be undone.`)) return;
    await list.run(async () => {
      await deleteAdminConversation(conversation.conversationId);
      setTranscript(null);
      await list.reload();
    });
  };

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={list.working} onRefresh={() => void list.reload()} />
      <AdminError error={list.error} />
      <AdminSearch
        placeholder="Search chat title"
        busy={list.working}
        onSearch={(query) => {
          list.setOffset(0);
          setSearch(query);
        }}
      />
      <AdminWorkspace
        detail={
          transcript && (
            <>
              <DetailHead title={transcript.conversation.title} onClose={() => setTranscript(null)} />
              <FieldLine label="Workspace">
                <span className="mono">{transcript.conversation.workspaceKey}</span>
              </FieldLine>
              <FieldLine label="Turns">{transcript.turns.length}</FieldLine>
              <FieldLine label="Updated">{formatTimestamp(transcript.conversation.updatedAt)}</FieldLine>
              <button
                type="button"
                className="danger-button"
                disabled={list.working}
                onClick={() => void deleteConversation(transcript.conversation)}
              >
                Delete conversation
              </button>
              <div className="admin-transcript">
                {transcript.turns.map((turn) => (
                  <article key={`${turn.sequence}-${turn.role}`} className="admin-turn">
                    <header>
                      Turn {turn.sequence} · {turn.role} · {turn.status}
                    </header>
                    <p>{turn.text}</p>
                  </article>
                ))}
              </div>
            </>
          )
        }
      >
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Workspace</th>
                <th>Turns</th>
                <th>Checkpoints</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.rows.map((conversation) => (
                <tr key={conversation.conversationId}>
                  <td>{conversation.title}</td>
                  <td className="mono">{conversation.workspaceKey}</td>
                  <td>{conversation.turnCount}</td>
                  <td>{conversation.summaryCount}</td>
                  <td>{formatTimestamp(conversation.updatedAt)}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => void openTranscript(conversation)}
                    >
                      Open (audited)
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager offset={list.offset} count={list.rows.length} busy={list.working} onOffset={list.setOffset} />
      </AdminWorkspace>
    </>
  );
}
