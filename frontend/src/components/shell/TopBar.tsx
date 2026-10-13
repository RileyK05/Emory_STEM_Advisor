import type { AuthSession } from '../../api/types';
import { MenuIcon } from '../icons';
import { MascotToggle } from './MascotToggle';

interface Props {
  session?: AuthSession;
  onLogout?: () => void;
  onMenu?: () => void;
}

export function TopBar({ session, onLogout, onMenu }: Props) {
  const identity = session
    ? session.mode === 'development'
      ? 'Local development'
      : session.displayName || session.email || 'Signed in'
    : null;
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
        {identity && <span className="identity">{identity}</span>}
        {onLogout && (
          <button type="button" className="topbar-button" onClick={onLogout}>
            Sign out
          </button>
        )}
        <MascotToggle />
      </div>
    </header>
  );
}
