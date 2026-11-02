import { useCallback, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { AuthSession } from '../../api/types';
import { useDismiss } from '../../lib/useDismiss';
import { saveBlob } from '../artifact/download';
import { ChevronDownIcon, MenuIcon } from '../icons';
import { MascotToggle } from './MascotToggle';

interface Props {
  session?: AuthSession;
  onLogout?: () => void;
  onMenu?: () => void;
  /** Admins only: whether the answer Details view is shown. */
  details?: boolean;
  onToggleDetails?: () => void;
}

export function TopBar({ session, onLogout, onMenu, details = false, onToggleDetails }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    setConfirmDelete(false);
  }, []);
  useDismiss(ref, open, close);

  const identity = session
    ? session.mode === 'development'
      ? 'Local development'
      : session.displayName || session.email || 'Signed in'
    : null;

  const download = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const created = await getClient().createPrivacyExport();
      if (!created.downloadToken) throw new Error('The export did not return a download token.');
      const download = await getClient().downloadPrivacyExport(created.exportId, created.downloadToken);
      saveBlob({
        blob: new Blob([JSON.stringify(download.manifest, null, 2)], { type: 'application/json' }),
        filename: `emory-advising-data-${created.exportId}.json`,
      });
      setMessage('Your data export was downloaded.');
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'The data export could not be created.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await getClient().deleteMyData({ confirmation: 'DELETE MY DATA', reason: 'user_request' });
      setMessage('Your data was deleted.');
      onLogout?.();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'Your data could not be deleted.');
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  };

  return (
    <header className="topbar">
      {onMenu && (
        <button type="button" className="icon-button topbar-menu" aria-label="Open menu" onClick={onMenu}>
          <MenuIcon />
        </button>
      )}
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          E
        </span>
        <h1>
          <span className="brand-emory">Emory</span> <span className="brand-name">STEM Advisor</span>
        </h1>
      </div>
      <div className="topbar-actions">
        {identity && (
          <div className="account-menu" ref={ref}>
            <button
              type="button"
              className="topbar-button account-trigger"
              aria-haspopup="menu"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              {identity}
              <ChevronDownIcon size={14} />
            </button>
            {open && (
              <div className="popover menu menu-right account-popover" role="menu">
                <div className="account-theme">
                  <span>Theme</span>
                  <MascotToggle />
                </div>
                <button type="button" role="menuitem" disabled={busy} onClick={() => void download()}>
                  Download my data
                </button>
                {!confirmDelete ? (
                  <button type="button" role="menuitem" onClick={() => setConfirmDelete(true)}>
                    Delete my data
                  </button>
                ) : (
                  <div className="account-confirm" role="group" aria-label="Confirm data deletion">
                    <p>Delete all chats, artifacts and private data? This cannot be undone.</p>
                    <button type="button" className="destructive-button" disabled={busy} onClick={() => void remove()}>
                      Delete permanently
                    </button>
                    <button type="button" className="link-button" onClick={() => setConfirmDelete(false)}>
                      Cancel
                    </button>
                  </div>
                )}
                {session?.role === 'admin' && (
                  <>
                    {onToggleDetails && (
                      <button type="button" role="menuitemcheckbox" aria-checked={details} onClick={onToggleDetails}>
                        Answer details
                      </button>
                    )}
                    <a className="account-link" href="/admin.html" role="menuitem">
                      Admin
                    </a>
                  </>
                )}
                {onLogout && (
                  <button type="button" role="menuitem" onClick={onLogout}>
                    Sign out
                  </button>
                )}
                {message && (
                  <p className="account-message" role="status">
                    {message}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
