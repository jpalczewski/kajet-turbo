import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import ts from 'typescript-eslint';
import svelteConfig from './svelte.config.js';

export default ts.config(
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  prettier,
  ...svelte.configs.prettier,
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
  {
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          args: 'all',
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
    },
  },
  {
    // Type-aware: catches conditions the types prove can never change, like a
    // status check after a client call that already throws on non-2xx (#397).
    // Scoped to the files the Svelte block below already type-checks: enabling
    // projectService for plain .ts too made the whole lint ~6x slower.
    files: ['src/**/*.svelte', 'src/**/*.svelte.ts'],
    rules: {
      '@typescript-eslint/no-unnecessary-condition': 'error',
    },
  },
  {
    files: ['**/*.svelte', '**/*.svelte.ts', '**/*.svelte.js'],
    languageOptions: {
      parserOptions: {
        projectService: true,
        extraFileExtensions: ['.svelte'],
        parser: ts.parser,
        svelteConfig,
      },
    },
  },
  {
    ignores: ['.svelte-kit/', 'node_modules/', 'src/lib/api/index.ts', 'orval.config.ts'],
  },
);
