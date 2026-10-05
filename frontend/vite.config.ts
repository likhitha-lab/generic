import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { defineConfig } from 'vite';

// No more `define: { 'process.env.GEMINI_API_KEY': ... }` here - the frontend
// no longer talks to Gemini directly (see src/api/client.ts). Vite already
// exposes any VITE_-prefixed variable via import.meta.env automatically.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
  },
});
