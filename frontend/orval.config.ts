import { defineConfig } from 'orval';

export default defineConfig({
  kajetTurbo: {
    input: '../openapi.json',
    output: {
      target: './src/lib/api/index.ts',
      client: 'fetch',
      override: {
        mutator: {
          path: './src/lib/api/fetcher.ts',
          name: 'customFetch',
        },
        // customFetch throws on any non-2xx response, so a resolved call is always a
        // success -- type it that way instead of as a Success | Error union callers
        // would have to narrow with unreachable status checks. Keep in sync with
        // fetcher.ts: if it ever stops throwing, this must go.
        fetch: { forceSuccessResponse: true },
      },
    },
  },
});
