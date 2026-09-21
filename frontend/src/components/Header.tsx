import { useApp } from '../state/AppContext';

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
    </svg>
  );
}

export function Header() {
  const { debugMode, toggleDebugMode, theme, toggleTheme } = useApp();
  return (
    <header className="app-header">
      <div className="app-header-title">
        <h1>Emory STEM Advisor</h1>
        <span className="app-header-sub">Answers grounded in your program documents</span>
      </div>
      <div className="app-header-actions">
        <button
          type="button"
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === 'light' ? 'Switch to dark mode (Dooley)' : 'Switch to light mode (Swoop)'}
          aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
        >
          {theme === 'light' ? <MoonIcon /> : <SunIcon />}
        </button>
        {debugMode && <span className="debug-badge">DEBUG MODE</span>}
        <label className="debug-toggle">
          <span>Debug</span>
          <button
            type="button"
            role="switch"
            aria-checked={debugMode}
            className={`switch ${debugMode ? 'switch-on' : ''}`}
            onClick={toggleDebugMode}
          >
            <span className="switch-knob" />
          </button>
        </label>
      </div>
    </header>
  );
}
