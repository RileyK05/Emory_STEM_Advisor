import React from 'react';
import ReactDOM from 'react-dom/client';
import { ThemeProvider } from '../state/ThemeContext';
import { AdminApp } from './AdminApp';
import '../styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AdminApp />
    </ThemeProvider>
  </React.StrictMode>,
);
