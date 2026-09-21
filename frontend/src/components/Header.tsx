import { useApp } from '../state/AppContext';

export function Header() {
  const { debugMode, toggleDebugMode } = useApp();
  return (
    <header className="app-header">
      <div className="app-header-title">
        <h1>Emory STEM Advisor</h1>
        <span className="app-header-sub">Answers grounded in your program documents</span>
      </div>
      <div className="app-header-actions">
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
