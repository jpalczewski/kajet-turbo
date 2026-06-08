import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/mcp': 'http://localhost:8000',
      '/authorize': 'http://localhost:8000',
      '/token': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
})
