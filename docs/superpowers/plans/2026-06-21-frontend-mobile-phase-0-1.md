# Frontend Mobile — Phases 0–1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sprawić, by aplikacja przestała być ucinana przez Safari i by explorer notatek był używalny na iPhonie (drill-down: folder → notatka), z fundamentem testów `vitest` pod wydzieloną logikę.

**Architecture:** Globalne mobile-fixy (`100dvh`, safe-area, breakpointy) + responsywny navbar (Faza 0). Następnie czysta logika wydzielona z komponentów do testowanych modułów (`breadcrumb`, `tree.childFolders`, `explorerView`) i responsywna kompozycja explorera: desktop bez zmian (grid 3-kol), mobile pokazuje jeden panel naraz (`activePane`), a widok folderu scala podfoldery (`childFolders`) z listą notatek (Faza 1).

**Tech Stack:** SvelteKit 5 (runes), TypeScript, SCSS (moduł per komponent), `vitest` (env node), `bun`.

**Scope:** Tylko Fazy 0–1 ze speca `2026-06-21-frontend-mobile-overhaul-design.md`. Faza 2 (prymitywy UI: `Modal/Sheet`, `Field`, `Button`, `IconButton`, `Chip`, `ListRow`; `MoveNoteDialog`→Sheet; `TagEditor`→`tagEditor.ts`) i Faza 3 (pozostałe route'y: `settings`, `edit`, `history`, `chunks`, `workspaces`; `noteLinks.ts`) — osobny plan po zakończeniu tego.

**Konwencje repo:** lint/format `bun run lint` / `bun run format`; type-check `bun run check`; commit prefixy `feat:`/`fix:`/`refactor:`/`style:`; kod i komentarze po angielsku. Po każdym tasku uruchom `bun run check` przed commitem.

---

## File Structure

Nowe pliki:
- `frontend/vitest.config.ts` — konfiguracja testów (env node, alias `$lib`).
- `frontend/src/lib/breadcrumb.ts` — czyste funkcje breadcrumb (`breadcrumbCrumbs`, `parentFolder`).
- `frontend/src/lib/breadcrumb.test.ts` — testy.
- `frontend/src/lib/explorerView.ts` — `activePane()` (wybór panelu na mobile).
- `frontend/src/lib/explorerView.test.ts` — testy.
- `frontend/src/lib/styles/_breakpoints.scss` — mixiny responsywne (`mobile`, `tablet-down`, `hover`, `touch`).
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.test.ts` — testy `buildTree`, `ancestors`, `childFolders`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte` — mobilny nagłówek folderu (tryb Pliki/Tagi + breadcrumb „w górę" + podfoldery + ustawienia).

Modyfikowane:
- `frontend/package.json` — skrypt `test`, devDep `vitest`.
- `frontend/src/app.html` — `viewport-fit=cover`.
- `frontend/src/lib/styles/_utils.scss` — `100vh` → `100dvh`.
- `frontend/src/lib/styles/_variables.scss` — `$bp-mobile`, `$bp-tablet`.
- `frontend/src/lib/components/Navbar.svelte` — safe-area + responsywność.
- `frontend/src/lib/components/Breadcrumb.svelte` — użycie `breadcrumbCrumbs`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.ts` — `childFolders()`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.ts` — `noteSelected` w `load`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte` — responsywna kompozycja + `MobileFolderNav` + back na mobile.

---

# Faza 0 — Tani unblock

## Task 1: Harness `vitest`

**Files:**
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.test.ts`
- Modify: `frontend/package.json`

- [ ] **Step 1: Dodaj `vitest`**

Run (w katalogu `frontend/`):
```bash
bun add -d vitest
```

- [ ] **Step 2: Konfiguracja vitest (env node, alias `$lib`)**

Create `frontend/vitest.config.ts`:
```ts
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
```

- [ ] **Step 3: Skrypt `test` w `package.json`**

W `frontend/package.json`, w sekcji `"scripts"`, dodaj po linii `"check": ...`:
```json
    "test": "vitest run",
    "test:watch": "vitest",
```

- [ ] **Step 4: Pierwszy test (dowód działania harnessu)**

Create `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { ancestors, buildTree } from './tree';

describe('buildTree', () => {
  it('nests children under their parent folder', () => {
    const tree = buildTree(['a', 'b', 'a/x']);
    expect(tree.map((n) => n.fullPath)).toEqual(['a', 'b']);
    const a = tree.find((n) => n.fullPath === 'a')!;
    expect(a.children.map((n) => n.fullPath)).toEqual(['a/x']);
  });
});

describe('ancestors', () => {
  it('returns each ancestor path including the folder itself', () => {
    expect(ancestors('a/b/c')).toEqual(['a', 'a/b', 'a/b/c']);
  });
  it('returns empty for the root', () => {
    expect(ancestors('')).toEqual([]);
  });
});
```

- [ ] **Step 5: Uruchom testy — mają przejść**

Run: `bun run test`
Expected: PASS, 3 testy zielone.

- [ ] **Step 6: Commit**

```bash
# stages package.json + lockfile (bun.lock/bun.lockb) + config + test in one go
git add frontend/
git commit -m "test: add vitest harness for frontend logic"
```

---

## Task 2: Breakpointy i tokeny responsywne

**Files:**
- Modify: `frontend/src/lib/styles/_variables.scss`
- Create: `frontend/src/lib/styles/_breakpoints.scss`

- [ ] **Step 1: Dodaj zmienne breakpointów**

W `frontend/src/lib/styles/_variables.scss`, na końcu pliku dodaj:
```scss

$bp-mobile: 768px;
$bp-tablet: 1024px;
```

- [ ] **Step 2: Mixiny responsywne**

Create `frontend/src/lib/styles/_breakpoints.scss`:
```scss
@use 'variables' as v;

// Single source of truth for responsive boundaries. Usage:
//   @use '$lib/styles/breakpoints' as bp;
//   @include bp.mobile { ... }

@mixin mobile {
  @media (max-width: #{v.$bp-mobile - 1px}) {
    @content;
  }
}

@mixin tablet-down {
  @media (max-width: #{v.$bp-tablet - 1px}) {
    @content;
  }
}

// Apply hover affordances only on devices that actually hover (not touch),
// so taps on phones don't get stuck in a hover state.
@mixin hover {
  @media (hover: hover) {
    @content;
  }
}

@mixin touch {
  @media (hover: none) {
    @content;
  }
}
```

- [ ] **Step 3: Sanity — projekt się kompiluje**

Run: `bun run check`
Expected: brak nowych błędów (mixiny nieużywane jeszcze, ale plik musi się parsować — `_breakpoints.scss` jest importowany dopiero przez komponenty w kolejnych taskach; tu tylko sprawdzamy, że `_variables.scss` jest poprawny).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/styles/_variables.scss frontend/src/lib/styles/_breakpoints.scss
git commit -m "feat: responsive breakpoint tokens and mixins"
```

---

## Task 3: Globalne mobile-fixy (dvh + safe-area)

**Files:**
- Modify: `frontend/src/app.html`
- Modify: `frontend/src/lib/styles/_utils.scss:7`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte:127`

- [ ] **Step 1: `viewport-fit=cover` (odsłania safe-area-inset-*)**

W `frontend/src/app.html` zmień linię meta viewport na:
```html
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
```

- [ ] **Step 2: `.center` na `100dvh`**

W `frontend/src/lib/styles/_utils.scss`, w regule `.center` zmień:
```scss
  height: 100dvh;
```
(z `height: 100vh;`)

- [ ] **Step 3: Wysokość explorera na `100dvh`**

W `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`, w `.explorer` zmień:
```scss
    height: calc(100dvh - 48px);
```
(z `height: calc(100vh - 48px);`)

- [ ] **Step 4: Weryfikacja**

Run: `bun run check`
Expected: brak błędów.
Run: `bun run dev` i w DevTools (device toolbar, iPhone) sprawdź, że treść explorera nie jest ucinana paskiem Safari. (Wizualna weryfikacja — opcjonalna, jeśli środowisko pozwala.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app.html frontend/src/lib/styles/_utils.scss "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte"
git commit -m "fix: use dvh and viewport-fit=cover for mobile Safari"
```

---

## Task 4: Responsywny navbar

**Files:**
- Modify: `frontend/src/lib/components/Navbar.svelte`

- [ ] **Step 1: Dodaj import mixinów na początku bloku `<style>`**

W `frontend/src/lib/components/Navbar.svelte`, w `<style lang="scss">`, pod istniejącą linią `@use '$lib/styles/variables' as v;` dodaj:
```scss
  @use '$lib/styles/breakpoints' as bp;
```

- [ ] **Step 2: Safe-area + responsywne zwężenie**

W tym samym pliku, w regule `.navbar`, dodaj `padding-left`/`right` z safe-area oraz mobilny override. Zmień regułę `.navbar` na:
```scss
  .navbar {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    padding: 0 max(v.$space-lg, env(safe-area-inset-right)) 0 max(v.$space-lg, env(safe-area-inset-left));
    height: 48px;
    border-bottom: 1px solid v.$border;
    background: rgba(8, 8, 8, 0.95);
    backdrop-filter: blur(4px);
    position: sticky;
    top: 0;
    z-index: 50;

    @include bp.mobile {
      gap: v.$space-sm;
      padding: 0 max(v.$space-md, env(safe-area-inset-right)) 0 max(v.$space-md, env(safe-area-inset-left));
    }
  }
```

- [ ] **Step 3: Zwęź środkową sekcję na mobile**

W regule `.navbar__center` dodaj mobilny override — zmień ją na:
```scss
  .navbar__center {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    margin-right: auto;
    margin-left: v.$space-md;

    @include bp.mobile {
      gap: v.$space-sm;
      margin-left: v.$space-sm;
      min-width: 0;
    }
  }
```

- [ ] **Step 4: Weryfikacja**

Run: `bun run check`
Expected: brak błędów.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/Navbar.svelte
git commit -m "feat: responsive navbar with safe-area insets"
```

---

# Faza 1 — Responsywny explorer

## Task 5: `breadcrumb.ts` — czyste funkcje + refactor `Breadcrumb.svelte`

**Files:**
- Create: `frontend/src/lib/breadcrumb.ts`
- Create: `frontend/src/lib/breadcrumb.test.ts`
- Modify: `frontend/src/lib/components/Breadcrumb.svelte`

- [ ] **Step 1: Test (failing)**

Create `frontend/src/lib/breadcrumb.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { breadcrumbCrumbs, parentFolder } from './breadcrumb';

describe('breadcrumbCrumbs', () => {
  it('returns empty for the root', () => {
    expect(breadcrumbCrumbs('')).toEqual([]);
  });
  it('returns one crumb for a top-level folder', () => {
    expect(breadcrumbCrumbs('a')).toEqual([{ label: 'a', folder: 'a' }]);
  });
  it('accumulates folder paths segment by segment', () => {
    expect(breadcrumbCrumbs('a/b/c')).toEqual([
      { label: 'a', folder: 'a' },
      { label: 'b', folder: 'a/b' },
      { label: 'c', folder: 'a/b/c' },
    ]);
  });
});

describe('parentFolder', () => {
  it('drops the last segment', () => {
    expect(parentFolder('a/b/c')).toBe('a/b');
  });
  it('returns root for a top-level folder', () => {
    expect(parentFolder('a')).toBe('');
  });
  it('returns root for the root', () => {
    expect(parentFolder('')).toBe('');
  });
});
```

- [ ] **Step 2: Uruchom — ma się wywalić (brak modułu)**

Run: `bun run test`
Expected: FAIL — `Cannot find module './breadcrumb'`.

- [ ] **Step 3: Implementacja**

Create `frontend/src/lib/breadcrumb.ts`:
```ts
export type Crumb = { label: string; folder: string };

/** Folder split into cumulative breadcrumb crumbs; each `folder` is navigable. */
export function breadcrumbCrumbs(folder: string): Crumb[] {
  if (!folder) return [];
  const parts = folder.split('/');
  return parts.map((label, i) => ({ label, folder: parts.slice(0, i + 1).join('/') }));
}

/** Parent folder path of `folder` ('' at the root). */
export function parentFolder(folder: string): string {
  const i = folder.lastIndexOf('/');
  return i === -1 ? '' : folder.slice(0, i);
}
```

- [ ] **Step 4: Uruchom — ma przejść**

Run: `bun run test`
Expected: PASS.

- [ ] **Step 5: Refactor `Breadcrumb.svelte` na wspólną funkcję**

W `frontend/src/lib/components/Breadcrumb.svelte` zmień blok `<script>` — zamień obliczanie `segments` na crumbs:
```svelte
<script lang="ts">
  import { notesPath, workspacesPath } from '$lib/routes';
  import { breadcrumbCrumbs } from '$lib/breadcrumb';

  let {
    slug,
    folder = null,
    current = '',
  }: { slug: string; folder?: string | null; current?: string } = $props();

  const crumbs = $derived(breadcrumbCrumbs(folder ?? ''));
</script>
```
oraz w markupie zamień pętlę `{#each segments ...}` na:
```svelte
  {#each crumbs as crumb (crumb.folder)}
    <span class="breadcrumb__sep">/</span>
    <span class="breadcrumb__folder">{crumb.label}</span>
  {/each}
```

- [ ] **Step 6: Weryfikacja**

Run: `bun run check && bun run test`
Expected: brak błędów, testy zielone. Zachowanie `Breadcrumb` bez zmian (te same segmenty renderowane).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/breadcrumb.ts frontend/src/lib/breadcrumb.test.ts frontend/src/lib/components/Breadcrumb.svelte
git commit -m "refactor: extract breadcrumb logic to tested module"
```

---

## Task 6: `tree.childFolders()` — bezpośrednie podfoldery

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.ts`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.test.ts`

- [ ] **Step 1: Test (failing)**

W `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.test.ts` dodaj na górze import `childFolders`:
```ts
import { ancestors, buildTree, childFolders } from './tree';
```
i dopisz na końcu pliku:
```ts
describe('childFolders', () => {
  const all = ['a', 'b', 'a/x', 'a/y', 'a/x/z'];

  it('returns top-level folders for the root', () => {
    expect(childFolders(all, '')).toEqual(['a', 'b']);
  });
  it('returns immediate children of a folder', () => {
    expect(childFolders(all, 'a')).toEqual(['a/x', 'a/y']);
  });
  it('returns deeper immediate children', () => {
    expect(childFolders(all, 'a/x')).toEqual(['a/x/z']);
  });
  it('returns empty when there are no children', () => {
    expect(childFolders(all, 'b')).toEqual([]);
  });
});
```

- [ ] **Step 2: Uruchom — ma się wywalić**

Run: `bun run test`
Expected: FAIL — `childFolders is not a function` / brak eksportu.

- [ ] **Step 3: Implementacja**

W `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.ts` dopisz na końcu pliku:
```ts
/** Immediate child folder paths of `parent` ('' = root), sorted. */
export function childFolders(folders: string[], parent: string): string[] {
  const prefix = parent ? `${parent}/` : '';
  const depth = parent ? parent.split('/').length : 0;
  return folders
    .filter((f) => f.startsWith(prefix) && f.split('/').length === depth + 1)
    .sort();
}
```

- [ ] **Step 4: Uruchom — ma przejść**

Run: `bun run test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.ts" "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/tree.test.ts"
git commit -m "feat: childFolders helper for immediate subfolders"
```

---

## Task 7: `explorerView.activePane()` + `noteSelected` w `load`

**Files:**
- Create: `frontend/src/lib/explorerView.ts`
- Create: `frontend/src/lib/explorerView.test.ts`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.ts`

- [ ] **Step 1: Test (failing)**

Create `frontend/src/lib/explorerView.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { activePane } from './explorerView';

describe('activePane', () => {
  it('shows the preview when a note is explicitly selected', () => {
    expect(activePane({ noteSelected: true })).toBe('preview');
  });
  it('shows the list (folder view) when no note is selected', () => {
    expect(activePane({ noteSelected: false })).toBe('list');
  });
});
```

- [ ] **Step 2: Uruchom — ma się wywalić**

Run: `bun run test`
Expected: FAIL — brak modułu `./explorerView`.

- [ ] **Step 3: Implementacja**

Create `frontend/src/lib/explorerView.ts`:
```ts
export type ExplorerPane = 'list' | 'preview';

/**
 * Which explorer pane is the active one on a narrow (mobile) viewport.
 * `noteSelected` must come from `load` (the backend `ls` probe decides whether
 * the last URL segment is a folder or a note) — not from raw route params,
 * and not from `noteId` alone (a folder defaults its preview to its README,
 * which sets `noteId` without the user having selected a note).
 */
export function activePane(data: { noteSelected: boolean }): ExplorerPane {
  return data.noteSelected ? 'preview' : 'list';
}
```

- [ ] **Step 4: Uruchom — ma przejść**

Run: `bun run test`
Expected: PASS.

- [ ] **Step 5: Dodaj `noteSelected` do `load`**

W `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.ts`:

W gałęzi `view === 'tags'` (obiekt zwracany w `return { mode: 'tags' ... }`) dodaj pole:
```ts
      noteSelected: false,
```

W finalnym `return { mode: 'files' ... }` dodaj pole (tuż obok `noteId`):
```ts
    noteSelected: !isFolder,
```

- [ ] **Step 6: Weryfikacja**

Run: `bun run check && bun run test`
Expected: brak błędów, testy zielone.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/explorerView.ts frontend/src/lib/explorerView.test.ts "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.ts"
git commit -m "feat: activePane selector and noteSelected load flag"
```

---

## Task 8: Responsywna kompozycja explorera (mobile: jeden panel + back)

Na desktopie ≥768px bez zmian. Na mobile: sidebar (pełne drzewo) ukryty; widoczny tylko panel `list` **albo** `preview` zależnie od `activePane`; w trybie `preview` pasek „wstecz" wraca do folderu.

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`

- [ ] **Step 1: Import logiki + derywacja panelu**

W `<script>` pliku `+page.svelte` dodaj import (obok istniejących) i derywację:
```ts
  import { activePane } from '$lib/explorerView';
```
oraz pod `let slug = $derived(data.slug);`:
```ts
  let pane = $derived(activePane({ noteSelected: data.noteSelected }));
```

- [ ] **Step 2: Modyfikator klasy na kontenerze + back-bar w podglądzie**

Zmień otwarcie `<div class="explorer">` na:
```svelte
<div class="explorer" class:explorer--preview={pane === 'preview'}>
```

W sekcji `<section class="explorer__preview">` dodaj na początku (przed `<NotePreview ... />`) mobilny pasek powrotu:
```svelte
  <section class="explorer__preview">
    <a class="explorer__back" href={notesPath(slug, data.folderPath)}>‹ Wstecz</a>
    <NotePreview note={data.note} {slug} links={data.links} onmoved={handleMoveNote} />
  </section>
```
(`notesPath` jest już importowany; `data.folderPath` to bieżący folder, więc powrót zdejmuje notatkę z URL-a.)

- [ ] **Step 3: Import mixinów w `<style>`**

W bloku `<style lang="scss">`, pod `@use '$lib/styles/variables' as v;` dodaj:
```scss
  @use '$lib/styles/breakpoints' as bp;
```

- [ ] **Step 4: Reguły responsywne**

Na końcu bloku `<style>` (przed zamykającym `</style>`) dodaj:
```scss
  .explorer__back {
    display: none;
  }

  @include bp.mobile {
    .explorer {
      display: block;
      height: calc(100dvh - 48px);
      margin: 0;
      border: none;
      border-radius: 0;
      overflow-y: auto;
    }

    .explorer__sidebar {
      display: none;
    }

    .explorer__list,
    .explorer__preview {
      height: 100%;
    }

    // Mobile shows exactly one pane: list by default, preview when selected.
    .explorer__preview {
      display: none;
    }
    .explorer--preview .explorer__list {
      display: none;
    }
    .explorer--preview .explorer__preview {
      display: flex;
    }

    .explorer__back {
      display: block;
      flex-shrink: 0;
      padding: 10px 12px;
      border-bottom: 1px solid v.$border;
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$accent-dark;
      text-decoration: none;
    }
  }
```

- [ ] **Step 5: Weryfikacja**

Run: `bun run check`
Expected: brak błędów.
Run: `bun run dev`, DevTools iPhone: na folderze widać listę notatek (pełna szerokość); klik notatki → podgląd pełnoekranowy z paskiem „‹ Wstecz"; „Wstecz" wraca do listy. (Podfoldery jeszcze niedostępne na mobile — Task 9.)

- [ ] **Step 6: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte"
git commit -m "feat: single-pane mobile explorer with drill-down to preview"
```

---

## Task 9: `MobileFolderNav` — podfoldery + breadcrumb „w górę" na mobile

Na mobile sidebar (pełne drzewo) jest ukryty, więc nawigacja po folderach musi żyć w panelu listy: nagłówek z trybem Pliki/Tagi, breadcrumb z linkami w górę, lista bezpośrednich podfolderów (`childFolders`) jako wiersze „w głąb", oraz link do ustawień.

**Files:**
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`

- [ ] **Step 1: Komponent `MobileFolderNav.svelte`**

Create `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte`:
```svelte
<script lang="ts">
  import { notesPath, tagsPath, workspaceSettingsPath } from '$lib/routes';
  import { breadcrumbCrumbs } from '$lib/breadcrumb';
  import { childFolders } from './tree';
  import ExplorerModeToggle from './ExplorerModeToggle.svelte';

  let {
    slug,
    mode,
    folderPath,
    folders,
  }: {
    slug: string;
    mode: 'files' | 'tags';
    folderPath: string;
    folders: string[];
  } = $props();

  const crumbs = $derived(breadcrumbCrumbs(folderPath));
  const subfolders = $derived(childFolders(folders, folderPath));
</script>

<div class="mobile-folder-nav">
  <ExplorerModeToggle {slug} {mode} />

  {#if mode === 'files'}
    <nav class="crumbs">
      <a class="crumb" href={notesPath(slug)}>{slug}</a>
      {#each crumbs as crumb (crumb.folder)}
        <span class="crumb-sep">/</span>
        <a class="crumb" href={notesPath(slug, crumb.folder)}>{crumb.label}</a>
      {/each}
    </nav>

    {#if subfolders.length}
      <ul class="subfolders">
        {#each subfolders as folder (folder)}
          <li>
            <a class="subfolder" href={notesPath(slug, folder)}>
              <span class="subfolder__icon">📁</span>
              <span class="subfolder__name">{folder.split('/').at(-1)}/</span>
              <span class="subfolder__chevron">›</span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}

  <a class="settings" href={workspaceSettingsPath(slug)}>⚙ Ustawienia</a>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  // Mobile-only: the desktop sidebar provides this navigation on wide screens.
  .mobile-folder-nav {
    display: none;

    @include bp.mobile {
      display: block;
      border-bottom: 1px solid v.$border;
    }
  }

  .crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: v.$space-xs;
    padding: 0 12px 8px;
    font-family: v.$font-mono;
    font-size: 0.72rem;
  }
  .crumb {
    color: v.$accent-dark;
    text-decoration: none;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .crumb-sep {
    color: v.$text-muted;
  }

  .subfolders {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .subfolder {
    display: flex;
    align-items: center;
    gap: v.$space-sm;
    min-height: 44px;
    padding: 0 12px;
    border-top: 1px solid v.$border;
    color: v.$text-secondary;
    font-family: v.$font-mono;
    font-size: 0.85rem;
    text-decoration: none;

    &__name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    &__chevron {
      color: v.$text-muted;
    }
  }

  .settings {
    display: block;
    padding: 10px 12px;
    border-top: 1px solid v.$border;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$text-muted;
    text-decoration: none;
  }
</style>
```

- [ ] **Step 2: Wstaw `MobileFolderNav` do panelu listy**

W `+page.svelte` dodaj import w `<script>`:
```ts
  import MobileFolderNav from './MobileFolderNav.svelte';
```

W sekcji `<section class="explorer__list">` dodaj komponent jako pierwsze dziecko (przed blokiem `{#if data.mode === 'tags'}`):
```svelte
  <section class="explorer__list">
    <MobileFolderNav {slug} mode={data.mode} folderPath={data.folderPath} folders={data.tree.folders} />
    {#if data.mode === 'tags'}
```
(Komponent sam jest ukryty na desktopie przez `@include bp.mobile`.)

- [ ] **Step 3: Weryfikacja**

Run: `bun run check`
Expected: brak błędów.
Run: `bun run dev`, DevTools iPhone (tryb Pliki): w panelu listy na górze widać przełącznik Pliki/Tagi, breadcrumb z linkami w górę, listę podfolderów (≥44px) prowadzących w głąb, oraz „⚙ Ustawienia". Pełny drill-down: root → podfolder → notatka → wstecz działa bez sidebaru. Desktop bez zmian.

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte" "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte"
git commit -m "feat: mobile folder navigation (subfolders + breadcrumb)"
```

---

## Po Fazie 1

Explorer jest używalny na iPhonie: drill-down root → podfolder → notatka → wstecz, tryb Pliki/Tagi, tworzenie notatek/folderów działa jak na desktopie (inline inputy są pełnej szerokości w panelu listy). `MoveNoteDialog` nadal działa (szerokość `min(420px, 100vw-32px)` mieści się na telefonie) — konwersja na bottom-sheet to Faza 2.

**Następny plan (Faza 2–3):** prymitywy UI (`Modal/Sheet`, `Field`, `Button`, `IconButton`, `Chip`, `ListRow`), `MoveNoteDialog`→Sheet, `TagEditor`→`tagEditor.ts` (+ testy), przejścia mobilne dla `settings`/`edit`/`history`/`chunks`/`workspaces`, `noteLinks.ts` (+ testy).
