import { useEffect, useMemo, useRef, useState } from 'react';
import { getClient, isDemoMode } from '../../api/client';
import type { NativeArtifactSummary } from '../../api/types';
import { useApp } from '../../state/AppContext';
import { ArtifactPanel } from '../artifact/ArtifactPanel';
import { useArtifactWorkspace } from '../artifact/useArtifactWorkspace';
import { ChatView } from '../chat/ChatView';
import type { Attachment, ChatController } from '../chat/model';
import { useDemoChat } from '../chat/useDemoChat';
import { useSavedChat } from '../chat/useSavedChat';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

// The API mode is fixed for the page's lifetime, so this is the same hook on every render.
const useChat: () => ChatController = isDemoMode() ? useDemoChat : useSavedChat;

export function Shell({ onLogout, showDetails = false }: { onLogout?: () => void; showDetails?: boolean }) {
  const { session } = useApp();
  const chat = useChat();
  const referenced = useMemo(
    () => chat.entries.flatMap((entry) => (entry.role === 'user' ? (entry.attachments ?? []) : [])),
    [chat.entries],
  );
  const workspace = useArtifactWorkspace(session.workspaceKey ?? 'development', chat.activeId, referenced);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [autoSuppressed, setAutoSuppressed] = useState(false);
  const [artifacts, setArtifacts] = useState<NativeArtifactSummary[]>([]);
  const [artifactsLoading, setArtifactsLoading] = useState(true);
  const trackedRuns = useRef(new Set<string>());
  // The last turn sequence that was already in the open chat when it loaded.
  const historyThrough = useRef<{ chat: string | null; sequence: number }>({ chat: null, sequence: -1 });

  useEffect(() => {
    setAutoSuppressed(false);
  }, [workspace.record?.artifactId, workspace.record?.revisionId]);

  const refreshArtifacts = async () => {
    setArtifactsLoading(true);
    try {
      setArtifacts(await getClient().listArtifacts());
    } catch {
      setArtifacts([]);
    } finally {
      setArtifactsLoading(false);
    }
  };

  useEffect(() => {
    void refreshArtifacts();
  }, []);

  useEffect(() => {
    if (workspace.artifactChanged) void refreshArtifacts();
  }, [workspace.artifactChanged]);

  // An answer that arrives while its chat is open can start an artifact run; track it and open the
  // panel. Answers already in a chat when it loads (or pages in earlier) are history: their
  // artifacts stay in the sidebar list and do not reopen the panel.
  useEffect(() => {
    if (chat.loadingConversation) return;
    if (historyThrough.current.chat !== chat.activeId) {
      const sequences = chat.entries.map((entry) => entry.sequence ?? -1);
      const firstOpen = historyThrough.current.chat === null && historyThrough.current.sequence === -1;
      historyThrough.current = {
        chat: chat.activeId,
        // A brand-new chat's first answer must still be trackable; do not mark
        // its entries as history on the initial id assignment.
        sequence: firstOpen ? -1 : Math.max(-1, ...sequences),
      };
      if (!firstOpen) return;
    }
    for (const entry of chat.entries) {
      if (entry.role !== 'assistant' || !entry.artifactRun) continue;
      if ((entry.sequence ?? -1) <= historyThrough.current.sequence) continue;
      const { runId } = entry.artifactRun;
      if (trackedRuns.current.has(runId)) continue;
      trackedRuns.current.add(runId);
      workspace.trackRun(entry.artifactRun, chat.activeId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.entries, chat.activeId, chat.loadingConversation]);

  const autoAttachment: Attachment | null = workspace.record
    ? {
        artifactId: workspace.record.artifactId,
        revisionId: workspace.record.revisionId,
        title: workspace.record.title,
        kind: workspace.record.kind,
      }
    : null;
  const attachmentsForComposer =
    autoAttachment && !autoSuppressed
      ? [autoAttachment, ...attachments.filter((item) => item.artifactId !== autoAttachment.artifactId)]
      : attachments;

  const removeAttachment = (item: Attachment) => {
    if (autoAttachment && item.artifactId === autoAttachment.artifactId) {
      setAutoSuppressed(true);
      return;
    }
    setAttachments((current) => current.filter((existing) => existing.artifactId !== item.artifactId));
  };

  // Leaving a chat leaves its attachments and open artifact behind, so neither rides along on the
  // next chat's messages. A new chat getting its id is the same chat and keeps both.
  const leaveChat = () => {
    setAttachments([]);
    setAutoSuppressed(false);
    workspace.closeArtifact();
    workspace.setPanelOpen(false);
  };
  const chatForView: ChatController = {
    ...chat,
    open: (id) => {
      if (id !== chat.activeId) leaveChat();
      chat.open(id);
    },
    startNew: () => {
      leaveChat();
      chat.startNew();
    },
    // Archiving the open chat moves to a new one.
    archive:
      chat.archive &&
      ((id) => {
        if (id === chat.activeId) leaveChat();
        chat.archive?.(id);
      }),
    send: (items) => {
      chat.send(items, () => setAttachments([]));
    },
  };
  const showPanel = workspace.panelOpen && Boolean(workspace.record || workspace.runs.length);

  return (
    <div className={'shell' + (showPanel ? ' with-panel' : '') + (showPanel && workspace.record ? ' with-canvas' : '')}>
      <TopBar session={session} onLogout={onLogout} onMenu={() => setDrawerOpen(true)} />
      <div className="shell-body">
        <Sidebar
          chat={chatForView}
          artifacts={artifacts}
          artifactsLoading={artifactsLoading}
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          onOpenArtifact={(artifactId, revisionId) => {
            void workspace.openArtifact(artifactId, revisionId).catch(() => undefined);
          }}
          onRefreshArtifacts={() => void refreshArtifacts()}
        />
        <ChatView
          chat={chatForView}
          attachments={attachmentsForComposer}
          onRemoveAttachment={removeAttachment}
          showDetails={showDetails}
        />
        {showPanel && <ArtifactPanel workspace={workspace} />}
      </div>
    </div>
  );
}
