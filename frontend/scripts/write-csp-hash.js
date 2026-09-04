// Hashes the two pieces of inline content SvelteKit's static output needs
// CSP to allow, so the ingress Caddyfile can list them explicitly instead of
// falling back to 'unsafe-inline':
//
// - the hydration bootstrap <script> in dist/index.html. Its content
//   (including a per-build random variable name) changes on every build, so
//   the hash can't be hardcoded.
// - the style="..." attribute on SvelteKit's #svelte-announcer live region
//   (an a11y route-change announcer SvelteKit injects into every app; see
//   .svelte-kit/generated/root.svelte). It's fixed by the installed
//   @sveltejs/kit version rather than per-build, but pulling it from the
//   generated source instead of hardcoding it means an upstream SvelteKit
//   change updates the hash automatically instead of silently breaking CSP.
//
// The Dockerfile's ingress stage bakes both hashes into the Caddyfile at
// image build time and then deletes this output (see /Dockerfile, /Caddyfile).
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

function sha256(content) {
  return `sha256-${createHash('sha256').update(content, 'utf8').digest('base64')}`;
}

function extractOne(source, pattern, label) {
  const matches = [...source.matchAll(pattern)];
  if (matches.length !== 1) {
    throw new Error(
      `Expected exactly one ${label}, found ${matches.length}. ` +
        'Update this script (and the Caddyfile CSP) if the SvelteKit output changed.',
    );
  }
  return matches[0][1];
}

const distDir = resolve(process.cwd(), '..', 'dist');
const indexHtml = readFileSync(resolve(distDir, 'index.html'), 'utf8');
const scriptHash = sha256(
  extractOne(indexHtml, /<script>([\s\S]*?)<\/script>/g, 'inline <script> in dist/index.html'),
);

const rootSvelte = readFileSync(resolve('.svelte-kit/generated/root.svelte'), 'utf8');
const announcerStyleHash = sha256(
  extractOne(
    rootSvelte,
    /id="svelte-announcer"[^>]*\sstyle="([^"]*)"/g,
    '#svelte-announcer style attribute in .svelte-kit/generated/root.svelte',
  ),
);

writeFileSync(resolve(distDir, 'csp-script-hash.txt'), scriptHash);
writeFileSync(resolve(distDir, 'csp-style-hash.txt'), announcerStyleHash);
