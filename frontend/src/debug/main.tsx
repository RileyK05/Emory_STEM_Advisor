import React from 'react';
import ReactDOM from 'react-dom/client';
import { ThemeProvider } from '../state/ThemeContext';
import { DebugApp } from './DebugApp';
import '../styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <DebugApp />
    </ThemeProvider>
  </React.StrictMode>,
);
