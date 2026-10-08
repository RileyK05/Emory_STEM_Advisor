import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In dev, /api and /health proxy to the FastAPI backend so the frontend and
// backend share an origin (no CORS). Set VITE_API_BASE_URL to bypass the proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
});
