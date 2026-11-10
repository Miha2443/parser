import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  // Keep the browser Host so the API can validate same-origin login/CSRF.
  const proxy = { '/api': { target: env.DASHBOARD_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: false } };
  return { plugins: [react()], server: { port: 5173, strictPort: false, proxy }, preview: { proxy } };
});
