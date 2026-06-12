import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/mcp': 'http://localhost:8000',
      '/authorize': 'http://localhost:8000',
      '/token': 'http://localhost:8000',
    },
  },
});
