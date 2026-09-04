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
// image build time (see /Dockerfile, /Caddyfile). The hash files are written
// next to dist/, not inside it, so nothing needs to clean them out of the
// shipped static assets afterwards.
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

// Svelte templates carry HTML-entity-escaped attribute text (e.g. `&amp;`),
// but the browser hashes the *decoded* attribute value it parses into the
// DOM — so the source text has to be decoded the same way before hashing,
// or a future style value using one of these characters would compute a
// hash that never matches what the browser actually enforces.
function decodeHtmlAttr(value) {
  return value
    .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&(?:#39|apos);/g, "'")
    .replace(/&amp;/g, '&');
}

const projectDir = resolve(process.cwd());
const distDir = resolve(projectDir, '..', 'dist');
const indexHtml = readFileSync(resolve(distDir, 'index.html'), 'utf8');
const scriptHash = sha256(
  extractOne(indexHtml, /<script>([\s\S]*?)<\/script>/g, 'inline <script> in dist/index.html'),
);

const rootSvelte = readFileSync(resolve(projectDir, '.svelte-kit/generated/root.svelte'), 'utf8');
// Matched as a whole opening tag first, independent of attribute order, then
// the style value is pulled out of that tag — so a future SvelteKit release
// reordering attributes (or adding new ones) doesn't stop this from matching.
const announcerTag = extractOne(
  rootSvelte,
  /(<div\b[^>]*\bid="svelte-announcer"[^>]*>)/g,
  '#svelte-announcer opening tag in .svelte-kit/generated/root.svelte',
);
const announcerStyleHash = sha256(
  decodeHtmlAttr(extractOne(announcerTag, /\sstyle="([^"]*)"/g, 'style attribute on that tag')),
);

writeFileSync(resolve(projectDir, '..', 'csp-script-hash.txt'), scriptHash);
writeFileSync(resolve(projectDir, '..', 'csp-style-hash.txt'), announcerStyleHash);
