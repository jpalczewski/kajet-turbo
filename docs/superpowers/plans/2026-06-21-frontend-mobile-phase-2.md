# Frontend Mobile — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Domknąć funkcjonalność mobilną explorera: dialogi jako bottom-sheet na telefonie, wybór tagu na mobile, dotykowy `TagEditor` z wydzieloną testowaną logiką.

**Architecture:** Nowy prymityw `Modal` (na bazie natywnego `<dialog>`) staje się wyśrodkowanym dialogiem na desktopie i bottom-sheetem na mobile; `MoveNoteDialog` refaktoryzowany na niego. `MobileFolderNav` w trybie tagi renderuje istniejący `TagTree` (domyka lukę nawigacji po tagach). Logika `TagEditor` wydzielona do testowanego `tagEditor.ts`, a sam komponent dostaje dotykowe cele.

**Tech Stack:** SvelteKit 5 (runes + snippets), TypeScript, SCSS, `vitest` (env node), bun.

**Scope:** Faza 2 ze specu `docs/superpowers/specs/2026-06-21-frontend-mobile-phase-2-design.md`. Dedup prymitywów `Button`/`Field`/`Chip`/`ListRow` jest ODŁOŻONY — GitHub issue #13, poza tym planem.

**Konwencje:** kod/komentarze po angielsku; po każdym tasku `bun run check` (svelte-check) + `bun run test`; commit prefixy `feat:`/`refactor:`/`test:`.

---

## File Structure

Nowe:
- `frontend/src/lib/components/ui/Modal.svelte` — natywny `<dialog>` z `show()`/`close()`, slotami `default`/`actions`, bottom-sheet na mobile.
- `frontend/src/lib/components/ui/IconButton.svelte` — ikonowy przycisk, 44px hit-area na mobile.
- `frontend/src/lib/tagEditor.ts` — `computeCandidates`, `computeOptions` (czysta logika).
- `frontend/src/lib/tagEditor.test.ts` — testy.

Modyfikowane:
- `frontend/src/lib/components/MoveNoteDialog.svelte` — refactor na `Modal` + `IconButton`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte` — gałąź tagi z `TagTree`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte` — przekazanie `tags`/`tagPath`/`includeDescendants` do `MobileFolderNav`.
- `frontend/src/lib/components/TagEditor.svelte` — użycie `tagEditor.ts` + dotykowość.

---

## Task 1: Prymitywy `Modal` + `IconButton`

Komponenty prezentacyjne (bez testów jednostkowych — to UI). Po tym tasku kompilują się, ale nie mają jeszcze konsumenta (wpięcie w Task 2).

**Files:**
- Create: `frontend/src/lib/components/ui/IconButton.svelte`
- Create: `frontend/src/lib/components/ui/Modal.svelte`

- [ ] **Step 1: `IconButton.svelte`**

Create `frontend/src/lib/components/ui/IconButton.svelte`:
```svelte
<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    label,
    onclick,
    children,
  }: {
    label: string;
    onclick: (event: MouseEvent) => void;
    children: Snippet;
  } = $props();
</script>

<button class="icon-button" type="button" aria-label={label} {onclick}>
  {@render children()}
</button>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .icon-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: none;
    background: none;
    color: v.$text-muted;
    font-size: 1.25rem;
    line-height: 1;
    cursor: pointer;

    @include bp.hover {
      &:hover {
        color: v.$text-primary;
      }
    }

    @include bp.mobile {
      width: 44px;
      height: 44px;
    }
  }
</style>
```

- [ ] **Step 2: `Modal.svelte`**

Create `frontend/src/lib/components/ui/Modal.svelte`:
```svelte
<script lang="ts">
  import type { Snippet } from 'svelte';
  import IconButton from './IconButton.svelte';

  let {
    title,
    children,
    actions,
    onclose,
  }: {
    title: string;
    children: Snippet;
    actions?: Snippet;
    onclose?: () => void;
  } = $props();

  let dialog: HTMLDialogElement;

  export function show() {
    dialog.showModal();
  }
  export function close() {
    dialog.close();
  }
</script>

<dialog bind:this={dialog} class="modal" onclick={(e) => e.target === dialog && dialog.close()} {onclose}>
  <div class="modal__content">
    <header class="modal__header">
      <span class="modal__handle"></span>
      <h2 class="modal__title">{title}</h2>
      <IconButton label="Zamknij" onclick={() => dialog.close()}>×</IconButton>
    </header>
    <div class="modal__body">
      {@render children()}
    </div>
    {#if actions}
      <div class="modal__actions">
        {@render actions()}
      </div>
    {/if}
  </div>
</dialog>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .modal {
    width: min(420px, calc(100vw - 32px));
    padding: 0;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    background: v.$bg-raised;
    color: v.$text-primary;

    &::backdrop {
      background: rgba(0, 0, 0, 0.72);
    }
  }

  .modal__content {
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
    padding: v.$space-lg;
  }

  .modal__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: v.$space-md;
  }

  .modal__title {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 1rem;
  }

  .modal__body {
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
  }

  .modal__handle {
    display: none;
  }

  .modal__actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: v.$space-sm;
  }

  @include bp.mobile {
    // Native modal <dialog> is centered via `inset:0; margin:auto`; re-pin to bottom as a sheet.
    .modal {
      width: 100%;
      max-width: 100%;
      margin: 0;
      inset: auto 0 0 0;
      border: none;
      border-top: 1px solid v.$border-accent;
      border-radius: v.$radius-lg v.$radius-lg 0 0;
    }

    .modal__content {
      padding-bottom: calc(#{v.$space-lg} + env(safe-area-inset-bottom));
    }

    .modal__header {
      position: relative;
      padding-top: v.$space-sm;
    }

    .modal__handle {
      display: block;
      position: absolute;
      top: 0;
      left: 50%;
      transform: translateX(-50%);
      width: 32px;
      height: 4px;
      border-radius: 2px;
      background: v.$text-muted;
    }
  }
</style>
```

- [ ] **Step 3: Verify**

Run: `bun run check` (in `frontend/`).
Expected: 0 errors. (`IconButton` is consumed by `Modal`; `Modal` is not consumed yet but must compile.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/ui/IconButton.svelte frontend/src/lib/components/ui/Modal.svelte
git commit -m "feat: Modal (bottom-sheet on mobile) and IconButton primitives"
```

---

## Task 2: Refactor `MoveNoteDialog` na `Modal` + `IconButton`

Zachowanie i wygląd na desktopie bez zmian; na mobile dialog staje się bottom-sheetem (z `Modal`). The whole component is rewritten to delegate chrome to `Modal`.

**Files:**
- Modify: `frontend/src/lib/components/MoveNoteDialog.svelte` (full rewrite)

- [ ] **Step 1: Rewrite `MoveNoteDialog.svelte`**

Replace the ENTIRE contents of `frontend/src/lib/components/MoveNoteDialog.svelte` with:
```svelte
<script lang="ts">
  import {
    apiLsApiWorkspacesNameLsGet,
    apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import Modal from '$lib/components/ui/Modal.svelte';

  let {
    slug,
    noteId,
    currentFolder,
    onmoved,
  }: {
    slug: string;
    noteId: string;
    currentFolder: string;
    onmoved: (folder: string) => void | Promise<void>;
  } = $props();

  let modal: Modal;
  let folders = $state<string[]>([]);
  let destination = $state('');
  let loading = $state(false);
  let moving = $state(false);
  let error = $state('');

  async function openDialog() {
    loading = true;
    error = '';
    destination = '';
    modal.show();
    try {
      const result = await apiLsApiWorkspacesNameLsGet(slug, { recursive: true });
      if (result.status !== 200) throw new Error();
      folders = ['', ...(result.data.folders ?? [])].filter((folder) => folder !== currentFolder);
      destination = folders[0] ?? '';
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się pobrać folderów');
    } finally {
      loading = false;
    }
  }

  async function moveNote() {
    if (loading || moving || folders.length === 0) return;
    moving = true;
    error = '';
    try {
      const result = await apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost(
        slug,
        noteId,
        jsonBody({ folder: destination }),
      );
      if (result.status !== 200) throw new Error();
      await onmoved(result.data.folder);
      modal.close();
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się przenieść notatki');
    } finally {
      moving = false;
    }
  }
</script>

<button class="move-trigger" onclick={openDialog}>Przenieś</button>

<Modal bind:this={modal} title="Przenieś notatkę">
  {#if loading}
    <p class="move-status">Ładowanie folderów…</p>
  {:else if folders.length === 0}
    <p class="move-status">Brak innych folderów docelowych.</p>
  {:else}
    <label class="move-field">
      Folder docelowy
      <select bind:value={destination}>
        {#each folders as folder (folder)}
          <option value={folder}>{folder || `${slug} (root)`}</option>
        {/each}
      </select>
    </label>
  {/if}

  {#if error}
    <p class="move-error">{error}</p>
  {/if}

  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={moving}>Anuluj</button>
    <button
      class="btn btn--primary"
      onclick={moveNote}
      disabled={loading || moving || folders.length === 0}
    >
      {moving ? 'Przenoszenie…' : 'Przenieś'}
    </button>
  {/snippet}
</Modal>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .move-trigger {
    padding: 0;
    border: none;
    background: none;
    color: v.$accent-dark;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    cursor: pointer;

    &:hover {
      color: v.$accent;
    }
  }

  .move-field {
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;

    select {
      width: 100%;
      padding: 9px 12px;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      background: v.$bg-surface;
      color: v.$text-primary;
      font-family: v.$font-mono;
    }
  }

  .move-status,
  .move-error {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
  }
  .move-status {
    color: v.$text-muted;
  }
  .move-error {
    color: v.$error;
  }
</style>
```

Notes for the implementer:
- The `.btn`/`.btn--primary`/`.btn--secondary` classes are GLOBAL (defined in `frontend/src/lib/styles/_buttons.scss`), so they work without local styles — same as before the refactor.
- `let modal: Modal;` types the component instance bound via `bind:this`; the `show()`/`close()` functions exported from `Modal.svelte` are callable on it. If svelte-check rejects `: Modal` as a type, use `let modal: ReturnType<typeof Modal>;` — but try `: Modal` first.
- The `{#snippet actions()}` block inside `<Modal>` is passed as the `actions` prop snippet.

- [ ] **Step 2: Verify**

Run: `bun run check && bun run test` (in `frontend/`).
Expected: 0 errors; tests still green (no test changes).

Manual (optional, if env allows): `bun run dev`, open a note, click "Przenieś" — on desktop a centered dialog (unchanged); in DevTools iPhone a bottom-sheet sliding from the bottom with a drag-handle; backdrop tap and × both close; move still works.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/MoveNoteDialog.svelte
git commit -m "refactor: MoveNoteDialog onto Modal (bottom-sheet on mobile)"
```

---

## Task 3: Tag-tree inline na mobile (`MobileFolderNav`)

W trybie tagi `MobileFolderNav` renderuje istniejący `TagTree`, domykając lukę wyboru tagu na telefonie.

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`

- [ ] **Step 1: Rozszerz `MobileFolderNav.svelte` o propsy tagów + gałąź tagi**

In `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte`:

Replace the `<script>` block with (adds `TagTree`/`TagNode` imports and three props):
```svelte
<script lang="ts">
  import type { TagNode } from '$lib/api';
  import { notesPath, workspaceSettingsPath } from '$lib/routes';
  import { breadcrumbCrumbs } from '$lib/breadcrumb';
  import { childFolders } from './tree';
  import ExplorerModeToggle from './ExplorerModeToggle.svelte';
  import TagTree from './TagTree.svelte';

  let {
    slug,
    mode,
    folderPath,
    folders,
    tags,
    currentTag,
    includeDescendants,
  }: {
    slug: string;
    mode: 'files' | 'tags';
    folderPath: string;
    folders: string[];
    tags: TagNode[];
    currentTag: string;
    includeDescendants: boolean;
  } = $props();

  const crumbs = $derived(breadcrumbCrumbs(folderPath));
  const subfolders = $derived(childFolders(folders, folderPath));
</script>
```

In the markup, change the `{#if mode === 'files'} ... {/if}` block to add a tags branch — replace the closing `{/if}` of that block (the one right before `<a class="settings" ...>`) so the structure becomes:
```svelte
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
  {:else if mode === 'tags'}
    <div class="tag-tree-wrap">
      <TagTree {tags} {currentTag} {includeDescendants} {slug} />
    </div>
  {/if}
```
(Only the `{:else if mode === 'tags'} ... ` branch is new; the files branch content is unchanged. Leave the `ExplorerModeToggle` line above and the `settings` link below untouched.)

Add the wrapper style — in the `<style>` block, after the `.crumbs` / `.crumb` rules (anywhere inside the stylesheet is fine), add:
```scss
  .tag-tree-wrap {
    border-top: 1px solid v.$border;
  }
```

- [ ] **Step 2: Przekaż propsy tagów w `+page.svelte`**

In `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`, change the `<MobileFolderNav ... />` element (currently passing `{slug} mode folderPath folders`) to also pass the tag props:
```svelte
    <MobileFolderNav
      {slug}
      mode={data.mode}
      folderPath={data.folderPath}
      folders={data.tree.folders}
      tags={data.tags}
      currentTag={data.tagPath}
      includeDescendants={data.includeDescendants}
    />
```
Do not change anything else.

- [ ] **Step 3: Verify**

Run: `bun run check && bun run test` (in `frontend/`).
Expected: 0 errors; tests green.

Manual (optional): `bun run dev`, DevTools iPhone, switch to "Tagi" — the tag tree now appears at the top of the list pane; tapping a tag shows its notes below; the "z podtagami" toggle (already in the tag-list-header) works. Desktop unchanged (`MobileFolderNav` hidden via `display:none`).

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/MobileFolderNav.svelte" "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte"
git commit -m "feat: mobile tag navigation (inline TagTree in tags mode)"
```

---

## Task 4: `tagEditor.ts` (TDD) + refactor `TagEditor` + dotykowość

**Files:**
- Create: `frontend/src/lib/tagEditor.ts`
- Create: `frontend/src/lib/tagEditor.test.ts`
- Modify: `frontend/src/lib/components/TagEditor.svelte`

- [ ] **Step 1: Failing test**

Create `frontend/src/lib/tagEditor.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { computeCandidates, computeOptions } from './tagEditor';

describe('computeCandidates', () => {
  it('normalizes, dedupes, and drops invalid suggestions', () => {
    expect(computeCandidates([], ['Work', 'work', 'dom', '', '##'])).toEqual(['work', 'dom']);
  });
  it('excludes suggestions already applied as tags', () => {
    expect(computeCandidates(['work'], ['Work', 'dom'])).toEqual(['dom']);
  });
});

describe('computeOptions', () => {
  it('returns nothing for an empty query', () => {
    expect(computeOptions('', ['work', 'dom'], [])).toEqual([]);
  });
  it('filters candidates by substring and offers a create option', () => {
    expect(computeOptions('wo', ['work', 'dom', 'workshop'], [])).toEqual([
      { value: 'work', isCreate: false },
      { value: 'workshop', isCreate: false },
      { value: 'wo', isCreate: true },
    ]);
  });
  it('suppresses the create option on an exact candidate match', () => {
    expect(computeOptions('work', ['work', 'workshop'], [])).toEqual([
      { value: 'work', isCreate: false },
      { value: 'workshop', isCreate: false },
    ]);
  });
  it('suppresses the create option when the tag is already applied', () => {
    expect(computeOptions('dom', ['x'], ['dom'])).toEqual([]);
  });
  it('caps matches at 8', () => {
    const candidates = ['aa', 'ab', 'ac', 'ad', 'ae', 'af', 'ag', 'ah', 'ai'];
    const options = computeOptions('a', candidates, []);
    expect(options.filter((o) => !o.isCreate)).toHaveLength(8);
  });
});
```

- [ ] **Step 2: Run, expect FAIL** (`bun run test` → no module `./tagEditor`).

- [ ] **Step 3: Implement `tagEditor.ts`**

Create `frontend/src/lib/tagEditor.ts`:
```ts
import { normalizeTag } from '$lib/tags';

export type TagOption = { value: string; isCreate: boolean };

/** Normalized, deduped suggestion paths that aren't already applied. */
export function computeCandidates(tags: string[], suggestions: string[]): string[] {
  const seen = new Set(tags);
  const out: string[] = [];
  for (const raw of suggestions) {
    const n = normalizeTag(raw);
    if (n && !seen.has(n)) {
      seen.add(n);
      out.push(n);
    }
  }
  return out;
}

/**
 * Dropdown options for the current query: up to 8 substring matches, plus a
 * "create" option when the query is a fresh, valid tag that isn't an exact
 * existing match and isn't already applied.
 */
export function computeOptions(query: string, candidates: string[], tags: string[]): TagOption[] {
  const normalizedQuery = normalizeTag(query);
  const needle = normalizedQuery ?? '';
  const matches = needle ? candidates.filter((c) => c.includes(needle)).slice(0, 8) : [];
  const opts: TagOption[] = matches.map((value) => ({ value, isCreate: false }));
  if (normalizedQuery && !tags.includes(normalizedQuery) && !matches.includes(normalizedQuery)) {
    opts.push({ value: normalizedQuery, isCreate: true });
  }
  return opts;
}
```

- [ ] **Step 4: Run, expect PASS** (`bun run test`).

- [ ] **Step 5: Refactor `TagEditor.svelte` script onto the module**

In `frontend/src/lib/components/TagEditor.svelte`, replace the `<script>` block top — the imports, the `type Option`, and the `candidates`/`options` `$derived.by` declarations — with the version below. Keep everything from `let showDropdown = ...` downward unchanged, EXCEPT the one type reference noted:

Replace:
```svelte
<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';
  import { normalizeTag } from '$lib/tags';
```
with:
```svelte
<script lang="ts">
  import { normalizeTag } from '$lib/tags';
  import { computeCandidates, computeOptions, type TagOption } from '$lib/tagEditor';
```

Delete the local `type Option = { value: string; isCreate: boolean };` line.

Replace the entire `let candidates = $derived.by(() => { ... });` block with:
```ts
  let candidates = $derived(computeCandidates(tags, suggestions));
```

Replace the entire `let options = $derived.by<Option[]>(() => { ... });` block with:
```ts
  let options = $derived(computeOptions(query, candidates, tags));
```

In `function selectOption(opt: Option)`, change the parameter type to `TagOption`:
```ts
  function selectOption(opt: TagOption) {
    addTag(opt.value);
  }
```
(`normalizeTag` is still imported and used by `addTag` — keep it.)

- [ ] **Step 6: Touch targets + hover-gating in `TagEditor.svelte` `<style>`**

Add the breakpoints import under the variables `@use` at the top of the `<style lang="scss">` block:
```scss
  @use '$lib/styles/breakpoints' as bp;
```

Replace the `.tag-input` rule with (adds a mobile min-height of 44px):
```scss
  .tag-input {
    width: 100%;
    box-sizing: border-box;
    padding: v.$space-xs v.$space-sm;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    color: v.$text-primary;
    background: v.$bg-raised;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;

    &::placeholder {
      color: v.$text-muted;
    }
    &:focus {
      outline: none;
      border-color: v.$accent-dark;
    }

    @include bp.mobile {
      min-height: 44px;
    }
  }
```

Replace the `.option` rule with (splits `:hover` out under `bp.hover`, keeps `.active` always, bumps mobile tap height):
```scss
  .option {
    display: block;
    width: 100%;
    padding: v.$space-xs v.$space-sm;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    text-align: left;
    background: none;
    border: none;
    color: v.$text-muted;
    cursor: pointer;

    &.active {
      background: v.$bg-raised;
      color: v.$accent;
    }

    @include bp.hover {
      &:hover {
        background: v.$bg-raised;
        color: v.$accent;
      }
    }

    @include bp.mobile {
      min-height: 44px;
    }
  }
```

Replace the `.chip-remove` rule with (gates the hover behind `bp.hover`; the chip remove stays visually compact — a 44px target would blow up inline chips, so this secondary action keeps its size):
```scss
  .chip-remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    width: 14px;
    height: 14px;
    font-size: 0.7rem;
    line-height: 1;
    background: none;
    border: none;
    color: v.$text-muted;
    cursor: pointer;

    @include bp.hover {
      &:hover {
        color: v.$accent;
      }
    }
  }
```

- [ ] **Step 7: Verify**

Run: `bun run check && bun run test` (in `frontend/`).
Expected: 0 errors; all tests pass (the 6 new `tagEditor` tests + existing).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/tagEditor.ts frontend/src/lib/tagEditor.test.ts frontend/src/lib/components/TagEditor.svelte
git commit -m "refactor: extract tagEditor logic to tested module; touch-friendly TagEditor"
```

---

## Po Fazie 2

Telefon: dialog przenoszenia jako bottom-sheet, wybór tagu działa (drzewo tagów inline), `TagEditor` dotykowy. Logika `tagEditor` przetestowana. Pozostały dedup (`Button`/`Field`/`Chip`/`ListRow`) i przejścia per-route dla `settings`/`edit`/`history`/`chunks`/`workspaces` — issue #13 oraz Faza 3 (osobny plan), plus `noteLinks.ts` przy `NoteLinksPanel`.
