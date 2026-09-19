import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Deliberately not the SvelteKit plugin: it drags in SSR/runtime machinery the tests don't
// need. The plain Svelte plugin compiles components and `.svelte.ts` runes modules, and the
// `$app/*` runtime modules are replaced with the small stubs in `src/test/stubs`.
const local = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  plugins: [svelte()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    setupFiles: ['src/test/setup.ts'],
  },
  resolve: {
    // Svelte's package exports resolve to the server build unless told otherwise.
    conditions: ['browser'],
    alias: {
      $lib: local('./src/lib'),
      '$app/paths': local('./src/test/stubs/app-paths.ts'),
      '$app/environment': local('./src/test/stubs/app-environment.ts'),
      '$app/navigation': local('./src/test/stubs/app-navigation.ts'),
    },
  },
});
