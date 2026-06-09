import { defineConfig } from 'orval';

export default defineConfig({
  kajetTurbo: {
    input: '../openapi.json',
    output: {
      target: './src/lib/api/index.ts',
      client: 'fetch',
    },
  },
});
