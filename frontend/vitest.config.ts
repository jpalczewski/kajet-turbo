import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Pure-logic tests only — no SvelteKit plugin (avoids SSR/runtime mocks).
// `$lib` alias is resolved manually so modules importing `$lib/*` work.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
  resolve: {
    alias: {
      $lib: fileURLToPath(new URL('./src/lib', import.meta.url)),
    },
  },
});
