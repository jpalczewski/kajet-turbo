# Note View Dedup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zdeduplikować trzy oddzielne renderowania notatki do współdzielonych klocków, dokładając przy okazji sidebar (Tagi·Outline·Backlinki·Wychodzące) i podgląd chunków w explorerze.

**Architecture:** Nowe komponenty w `$lib/components/note/` (`ChunkList`, `NoteMeta`, `NoteBody`, `NoteActions`, `NoteModeToggle`) + czysta logika `$lib/outline.ts` (`processHeadings`). Najpierw budujemy klocki izolowanie, potem składamy z nich `NotePreview` (explorer) i `note/[id]` (pełny widok); strona `/chunks` staje się cienkim wrapperem. Zero zmian backendu — wszystkie dane już istnieją.

**Tech Stack:** SvelteKit 5 (runes), TypeScript, SCSS, `vitest` (env node), bun.

**Scope:** Spec `docs/superpowers/specs/2026-06-22-note-view-dedup-design.md`. Responsywność tablet/desktop tego układu → Visual Plan 2 (nie tu). Dedup `Button`/`Field`/`Chip`/`ListRow` → issue #13.

**Konwencje:** kod po angielsku; **przed każdym commitem** `bunx prettier --write` TYLKO na zmienionych plikach (repo ma pre-existing drift gdzie indziej — nie ruszaj); po każdym tasku `bun run check` + `bun run test` (22 istniejące + nowe muszą być zielone).

**Typy API (istniejące, w `$lib/api`):** `ChunkPreviewResponse = { title; index_state; chunk_count; chunks: ChunkPreviewItem[] }`; `ChunkPreviewItem = { ordinal; header_path: string[]; char_count; embedded; content }`; `NoteLinkItem = { note_id; folder; title }`; `LinksResponse = { backlinks; outlinks }`.

---

## File Structure

Nowe:
- `frontend/src/lib/outline.ts` + `frontend/src/lib/outline.test.ts`
- `frontend/src/lib/components/note/ChunkList.svelte`
- `frontend/src/lib/components/note/NoteMeta.svelte`
- `frontend/src/lib/components/note/NoteActions.svelte`
- `frontend/src/lib/components/note/NoteModeToggle.svelte`
- `frontend/src/lib/components/note/NoteBody.svelte`

Modyfikowane:
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/chunks/+page.svelte` → wrapper na `ChunkList`.
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NotePreview.svelte` → kompozycja z klocków.
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte` → kompozycja z klocków.
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.ts` → dorzuca `outlinks`.

Usuwane (Task 6): `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NoteLinksPanel.svelte`.

---

## Task 1: `ChunkList` (wydzielenie z `/chunks`)

**Files:**
- Create: `frontend/src/lib/components/note/ChunkList.svelte`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/chunks/+page.svelte`

- [ ] **Step 1: Utwórz `ChunkList.svelte`**

Create `frontend/src/lib/components/note/ChunkList.svelte`:
```svelte
<script lang="ts">
  import type { ChunkPreviewResponse } from '$lib/api';

  let { preview, showHeader = false }: { preview: ChunkPreviewResponse; showHeader?: boolean } =
    $props();
</script>

{#if showHeader}
  <header class="chunks-header">
    <h2 class="chunks-header__title">{preview.title}</h2>
    <div class="chunks-header__meta">
      <span class="badge" class:badge--stale={preview.index_state !== 'indexed'}>
        {preview.index_state}
      </span>
      <span class="chunks-header__count">{preview.chunk_count} chunków</span>
    </div>
  </header>
{/if}

{#if preview.chunks.length === 0}
  <p class="empty">Brak chunków (pusta notatka).</p>
{:else}
  <ul class="chunk-list">
    {#each preview.chunks as chunk (chunk.ordinal)}
      <li class="chunk">
        <p class="chunk__breadcrumb">
          {chunk.header_path.length > 0 ? chunk.header_path.join(' › ') : '—'}
        </p>
        <p class="chunk__meta">
          #{chunk.ordinal} · {chunk.char_count} znaków · {chunk.embedded
            ? 'embedded ✓'
            : 'nie zaindeksowany'}
        </p>
        <pre class="chunk__content">{chunk.content}</pre>
      </li>
    {/each}
  </ul>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .chunks-header {
    margin-bottom: v.$space-xl;
    padding-bottom: v.$space-lg;
    border-bottom: 1px solid v.$border;

    &__title {
      font-family: v.$font-mono;
      font-size: 1.25rem;
      color: v.$text-primary;
      margin: 0 0 v.$space-sm 0;
    }

    &__meta {
      display: flex;
      align-items: center;
      gap: v.$space-md;
    }

    &__count {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }
  }

  .badge {
    font-size: 0.7rem;
    font-family: v.$font-mono;
    letter-spacing: 0.04em;
    padding: v.$space-xs v.$space-sm;
    border: 1px solid v.$accent-dark;
    border-radius: v.$radius-sm;
    color: v.$accent;

    &--stale {
      border-color: v.$text-muted;
      color: v.$text-muted;
    }
  }

  .empty {
    font-size: 0.85rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
  }

  .chunk-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
  }

  .chunk {
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    padding: v.$space-lg;

    &__breadcrumb {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
      margin: 0 0 v.$space-xs 0;
    }

    &__meta {
      font-size: 0.75rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      margin: 0 0 v.$space-md 0;
      letter-spacing: 0.03em;
    }

    &__content {
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$text-primary;
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
    }
  }
</style>
```

- [ ] **Step 2: Przerób stronę `/chunks` na wrapper**

Replace the ENTIRE contents of `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/chunks/+page.svelte` with:
```svelte
<script lang="ts">
  import ChunkList from '$lib/components/note/ChunkList.svelte';
  import { notePath } from '$lib/routes';

  let { data } = $props();
</script>

<main class="page">
  <a href={notePath(data.slug, data.noteId)} class="back-link">← Wróć do notatki</a>
  <ChunkList preview={data.preview} showHeader />
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 1100px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .back-link {
    display: inline-block;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    text-decoration: none;
    margin-bottom: v.$space-lg;
    transition: color 0.15s;
    &:hover {
      color: v.$accent;
    }
  }
</style>
```

- [ ] **Step 3: Weryfikacja**

Run: `bun run check && bun run test` (in `frontend/`). Expected: 0 errors; 22 tests pass. Strona `/chunks` wygląda jak wcześniej (markup przeniesiony 1:1).

- [ ] **Step 4: Commit**

```bash
bunx prettier --write src/lib/components/note/ChunkList.svelte "src/routes/(protected)/workspace/[slug]/note/[id]/chunks/+page.svelte"
git add frontend/src/lib/components/note/ChunkList.svelte "frontend/src/routes/(protected)/workspace/[slug]/note/[id]/chunks/+page.svelte"
git commit -m "refactor: extract ChunkList component; /chunks page becomes a wrapper"
```

---

## Task 2: `outline.ts` (TDD)

**Files:**
- Create: `frontend/src/lib/outline.ts`
- Create: `frontend/src/lib/outline.test.ts`

- [ ] **Step 1: Failing test**

Create `frontend/src/lib/outline.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { processHeadings } from './outline';

describe('processHeadings', () => {
  it('returns empty for content without headings', () => {
    const r = processHeadings('<p>hello</p>');
    expect(r.outline).toEqual([]);
    expect(r.html).toBe('<p>hello</p>');
  });

  it('extracts h1–h3 with slug ids and injects matching ids', () => {
    const r = processHeadings('<h1>Plan Q3</h1><h2>Priorytety</h2><h3>Ryzyka</h3>');
    expect(r.outline).toEqual([
      { level: 1, text: 'Plan Q3', id: 'plan-q3' },
      { level: 2, text: 'Priorytety', id: 'priorytety' },
      { level: 3, text: 'Ryzyka', id: 'ryzyka' },
    ]);
    expect(r.html).toBe(
      '<h1 id="plan-q3">Plan Q3</h1><h2 id="priorytety">Priorytety</h2><h3 id="ryzyka">Ryzyka</h3>',
    );
  });

  it('deduplicates colliding slugs', () => {
    const r = processHeadings('<h2>Plan</h2><h2>Plan</h2><h2>Plan</h2>');
    expect(r.outline.map((o) => o.id)).toEqual(['plan', 'plan-2', 'plan-3']);
  });

  it('keeps Polish diacritics in slugs', () => {
    const r = processHeadings('<h2>Zażółć gęślą</h2>');
    expect(r.outline[0]).toEqual({ level: 2, text: 'Zażółć gęślą', id: 'zażółć-gęślą' });
  });

  it('strips nested inline markup from heading text and slug', () => {
    const r = processHeadings('<h3>Use <code>code</code> here</h3>');
    expect(r.outline[0]).toEqual({ level: 3, text: 'Use code here', id: 'use-code-here' });
  });

  it('ignores h4 and deeper', () => {
    const r = processHeadings('<h4>Deep</h4>');
    expect(r.outline).toEqual([]);
    expect(r.html).toBe('<h4>Deep</h4>');
  });
});
```

- [ ] **Step 2: Run, expect FAIL** (`bun run test` → no module `./outline`).

- [ ] **Step 3: Implement `outline.ts`**

Create `frontend/src/lib/outline.ts`:
```ts
export type OutlineItem = { level: number; text: string; id: string };

const HEADING_RE = /<h([1-3])\b([^>]*)>([\s\S]*?)<\/h\1>/gi;

function stripTags(html: string): string {
  return html.replace(/<[^>]*>/g, '').trim();
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}

/**
 * Parses h1–h3 headings from server-rendered (markdown→bleach) HTML, slugifies
 * their text (unicode-aware, colliding slugs get `-2`, `-3`, …), and returns the
 * HTML with `id` attributes injected on those headings plus an outline using the
 * SAME ids — so anchor links from the outline resolve.
 */
export function processHeadings(html: string): { html: string; outline: OutlineItem[] } {
  const outline: OutlineItem[] = [];
  const seen = new Map<string, number>();

  const out = html.replace(HEADING_RE, (_match, lvl: string, attrs: string, inner: string) => {
    const level = Number(lvl);
    const text = stripTags(inner);
    const base = slugify(text) || 'section';
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    const id = count === 0 ? base : `${base}-${count + 1}`;
    outline.push({ level, text, id });
    const cleanedAttrs = attrs.replace(/\s+id="[^"]*"/i, '');
    return `<h${level}${cleanedAttrs} id="${id}">${inner}</h${level}>`;
  });

  return { html: out, outline };
}
```

- [ ] **Step 4: Run, expect PASS** (`bun run test`).

- [ ] **Step 5: Commit**

```bash
bunx prettier --write src/lib/outline.ts src/lib/outline.test.ts
git add frontend/src/lib/outline.ts frontend/src/lib/outline.test.ts
git commit -m "feat: processHeadings (outline + heading anchors) with tests"
```

---

## Task 3: `NoteMeta` (sidebar)

Standalone component; wpinany w kompozycje w Task 5/6.

**Files:**
- Create: `frontend/src/lib/components/note/NoteMeta.svelte`

- [ ] **Step 1: Utwórz `NoteMeta.svelte`**

Create `frontend/src/lib/components/note/NoteMeta.svelte`:
```svelte
<script lang="ts">
  import { browser } from '$app/environment';
  import type { NoteLinkItem } from '$lib/api';
  import type { OutlineItem } from '$lib/outline';
  import { noteInTreePath, tagsPath } from '$lib/routes';

  let {
    slug,
    tags,
    outline,
    backlinks,
    outlinks,
    showOutline = true,
  }: {
    slug: string;
    tags: string[];
    outline: OutlineItem[];
    backlinks: NoteLinkItem[];
    outlinks: NoteLinkItem[];
    showOutline?: boolean;
  } = $props();

  const STORAGE_KEY = 'kajet:note-meta-collapsed';
  let collapsed = $state(browser && localStorage.getItem(STORAGE_KEY) === '1');

  function toggle() {
    collapsed = !collapsed;
    if (browser) localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
  }
</script>

{#if collapsed}
  <button class="rail" onclick={toggle} title="Pokaż panel" aria-label="Pokaż panel">
    <span class="rail__label">Info</span>
  </button>
{:else}
  <aside class="meta">
    <div class="meta__head">
      <span class="meta__title">Info</span>
      <button class="meta__toggle" onclick={toggle} title="Zwiń" aria-label="Zwiń panel">»</button>
    </div>

    {#if tags.length > 0}
      <div class="meta__section">
        <h4 class="meta__heading">Tagi</h4>
        <div class="meta__tags">
          {#each tags as tag (tag)}
            <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
            <a class="meta__tag" href={tagsPath(slug, tag)}>#{tag}</a>
          {/each}
        </div>
      </div>
    {/if}

    {#if showOutline && outline.length > 0}
      <div class="meta__section">
        <h4 class="meta__heading">Outline</h4>
        <ul class="meta__outline">
          {#each outline as item (item.id)}
            <li style="padding-left: {(item.level - 1) * 10}px">
              <a class="meta__anchor" href={`#${item.id}`}>{item.text}</a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    {#if backlinks.length > 0}
      <div class="meta__section">
        <h4 class="meta__heading">Backlinki ({backlinks.length})</h4>
        <ul class="meta__list">
          {#each backlinks as link (link.note_id)}
            <li>
              <a href={noteInTreePath(slug, link.folder, link.note_id)} class="meta__link">
                {#if link.folder}<span class="meta__folder">{link.folder}/</span>{/if}{link.title}
              </a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    {#if outlinks.length > 0}
      <div class="meta__section">
        <h4 class="meta__heading">Wychodzące ({outlinks.length})</h4>
        <ul class="meta__list">
          {#each outlinks as link (link.note_id)}
            <li>
              <a href={noteInTreePath(slug, link.folder, link.note_id)} class="meta__link">
                {#if link.folder}<span class="meta__folder">{link.folder}/</span>{/if}{link.title}
              </a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  </aside>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .rail {
    flex-shrink: 0;
    width: 28px;
    border: none;
    border-left: 1px solid v.$border;
    background: v.$bg-deep;
    color: v.$text-muted;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    &:hover {
      color: v.$accent;
    }
    &__label {
      writing-mode: vertical-rl;
      font-family: v.$font-mono;
      font-size: 0.7rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
  }

  .meta {
    flex-shrink: 0;
    width: 200px;
    border-left: 1px solid v.$border;
    background: v.$bg-deep;
    overflow-y: auto;
    display: flex;
    flex-direction: column;

    &__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 12px;
      border-bottom: 1px solid v.$border;
      position: sticky;
      top: 0;
      background: v.$bg-deep;
    }

    &__title {
      font-family: v.$font-mono;
      font-size: 0.7rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: v.$text-muted;
    }

    &__toggle {
      border: none;
      background: none;
      color: v.$text-muted;
      cursor: pointer;
      font-size: 0.9rem;
      line-height: 1;
      padding: 0 2px;
      &:hover {
        color: v.$accent;
      }
    }

    &__section {
      padding: v.$space-md 12px;
      & + & {
        border-top: 1px solid v.$border;
      }
    }

    &__heading {
      margin: 0 0 v.$space-sm 0;
      font-family: v.$font-mono;
      font-size: 0.68rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: v.$text-secondary;
    }

    &__tags {
      display: flex;
      flex-wrap: wrap;
      gap: v.$space-xs;
    }

    &__tag {
      font-family: v.$font-mono;
      font-size: 0.75rem;
      color: v.$accent-dark;
      text-decoration: none;
      &:hover {
        color: v.$accent;
      }
    }

    &__outline {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: v.$space-xs;
    }

    &__anchor {
      font-family: v.$font-mono;
      font-size: 0.75rem;
      color: v.$text-secondary;
      text-decoration: none;
      &:hover {
        color: v.$accent;
      }
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: v.$space-xs;
    }

    &__link {
      font-family: v.$font-mono;
      font-size: 0.78rem;
      color: v.$blue;
      text-decoration: none;
      word-break: break-word;
      &:hover {
        color: v.$blue-bright;
        text-decoration: underline;
      }
    }

    &__folder {
      color: v.$text-muted;
    }
  }
</style>
```

- [ ] **Step 2: Weryfikacja**

Run: `bun run check && bun run test` (in `frontend/`). Expected: 0 errors; 22 tests pass. Komponent kompiluje się (jeszcze nieużywany).

- [ ] **Step 3: Commit**

```bash
bunx prettier --write src/lib/components/note/NoteMeta.svelte
git add frontend/src/lib/components/note/NoteMeta.svelte
git commit -m "feat: NoteMeta sidebar (tags, outline, backlinks, outlinks)"
```

---

## Task 4: `NoteActions`, `NoteModeToggle`, `NoteBody`

Trzy prezentacyjne klocki; jeszcze nieużywane (wpinane w Task 5/6).

**Files:**
- Create: `frontend/src/lib/components/note/NoteActions.svelte`
- Create: `frontend/src/lib/components/note/NoteModeToggle.svelte`
- Create: `frontend/src/lib/components/note/NoteBody.svelte`

- [ ] **Step 1: `NoteActions.svelte`**

Create `frontend/src/lib/components/note/NoteActions.svelte`:
```svelte
<script lang="ts">
  import MoveNoteDialog from '$lib/components/MoveNoteDialog.svelte';
  import { noteChunksPath, noteEditPath, noteHistoryPath, notePath } from '$lib/routes';

  let {
    slug,
    noteId,
    folder,
    variant,
    onmoved,
  }: {
    slug: string;
    noteId: string;
    folder: string;
    variant: 'preview' | 'full';
    onmoved: (folder: string) => void | Promise<void>;
  } = $props();
</script>

<div class="actions">
  {#if variant === 'full'}
    <a href={noteEditPath(slug, noteId)} class="actions__link">Edytuj</a>
  {/if}
  <a href={noteHistoryPath(slug, noteId)} class="actions__link">Historia</a>
  <a href={noteChunksPath(slug, noteId)} class="actions__link">Chunki</a>
  <MoveNoteDialog {slug} {noteId} currentFolder={folder} {onmoved} />
  {#if variant === 'preview'}
    <a
      href={notePath(slug, noteId)}
      class="actions__link actions__link--primary"
      title="Otwórz pełny widok">↗</a
    >
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .actions {
    display: flex;
    align-items: center;
    gap: v.$space-sm;
    flex-shrink: 0;
  }

  .actions__link {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$text-muted;
    text-decoration: none;
    white-space: nowrap;
    &:hover {
      color: v.$text-primary;
    }
    &--primary {
      color: v.$accent-dark;
      font-size: 0.8rem;
      &:hover {
        color: v.$accent;
      }
    }
  }
</style>
```

- [ ] **Step 2: `NoteModeToggle.svelte`**

Create `frontend/src/lib/components/note/NoteModeToggle.svelte`:
```svelte
<script lang="ts">
  let { mode, onchange }: { mode: 'content' | 'chunks'; onchange: (m: 'content' | 'chunks') => void } =
    $props();
</script>

<div class="toggle" role="group" aria-label="Widok notatki">
  <button class="toggle__btn" class:active={mode === 'content'} onclick={() => onchange('content')}>
    Treść
  </button>
  <button class="toggle__btn" class:active={mode === 'chunks'} onclick={() => onchange('chunks')}>
    Chunki
  </button>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .toggle {
    display: flex;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    overflow: hidden;
    flex-shrink: 0;
  }

  .toggle__btn {
    background: none;
    border: none;
    color: v.$text-muted;
    font-family: v.$font-mono;
    font-size: 0.7rem;
    padding: 3px 9px;
    cursor: pointer;
    &.active {
      background: v.$bg-raised;
      color: v.$accent;
    }
    &:hover:not(.active) {
      color: v.$text-primary;
    }
  }
</style>
```

- [ ] **Step 3: `NoteBody.svelte`**

Create `frontend/src/lib/components/note/NoteBody.svelte`:
```svelte
<script lang="ts">
  import { apiGetNoteChunksApiWorkspacesNameNotesNoteIdChunksGet } from '$lib/api';
  import type { ChunkPreviewResponse } from '$lib/api';
  import Prose from '$lib/components/Prose.svelte';
  import ChunkList from './ChunkList.svelte';

  let {
    slug,
    noteId,
    html,
    mode,
  }: {
    slug: string;
    noteId: string;
    html: string;
    mode: 'content' | 'chunks';
  } = $props();

  let chunks = $state<ChunkPreviewResponse | null>(null);
  let loading = $state(false);
  let error = $state('');

  // Lazy-load chunks the first time the user switches to the chunks view.
  $effect(() => {
    if (mode === 'chunks' && chunks === null && !loading) {
      loading = true;
      error = '';
      apiGetNoteChunksApiWorkspacesNameNotesNoteIdChunksGet(slug, noteId)
        .then((result) => {
          if (result.status === 200) chunks = result.data;
          else error = 'Nie udało się pobrać chunków';
        })
        .catch(() => (error = 'Nie udało się pobrać chunków'))
        .finally(() => (loading = false));
    }
  });
</script>

{#if mode === 'content'}
  <Prose {html} />
{:else if loading}
  <p class="status">Ładowanie chunków…</p>
{:else if error}
  <p class="status status--error">{error}</p>
{:else if chunks}
  <ChunkList preview={chunks} />
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .status {
    font-family: v.$font-mono;
    font-size: 0.85rem;
    color: v.$text-muted;
    &--error {
      color: v.$error;
    }
  }
</style>
```

- [ ] **Step 4: Weryfikacja**

Run: `bun run check && bun run test` (in `frontend/`). Expected: 0 errors; 22 tests pass. Trzy komponenty kompilują się (jeszcze nieużywane).

- [ ] **Step 5: Commit**

```bash
bunx prettier --write src/lib/components/note/NoteActions.svelte src/lib/components/note/NoteModeToggle.svelte src/lib/components/note/NoteBody.svelte
git add frontend/src/lib/components/note/NoteActions.svelte frontend/src/lib/components/note/NoteModeToggle.svelte frontend/src/lib/components/note/NoteBody.svelte
git commit -m "feat: NoteActions, NoteModeToggle, NoteBody (lazy chunks) components"
```

---

## Task 5: Złożenie explorer preview (`NotePreview`)

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NotePreview.svelte`

- [ ] **Step 1: Przepisz `NotePreview.svelte` na klocki**

Replace the ENTIRE contents of `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NotePreview.svelte` with:
```svelte
<script lang="ts">
  import type { LinksResponse, NoteHtmlResponse } from '$lib/api';
  import NoteActions from '$lib/components/note/NoteActions.svelte';
  import NoteBody from '$lib/components/note/NoteBody.svelte';
  import NoteMeta from '$lib/components/note/NoteMeta.svelte';
  import NoteModeToggle from '$lib/components/note/NoteModeToggle.svelte';
  import { processHeadings } from '$lib/outline';

  let {
    note,
    slug,
    links,
    onmoved,
  }: {
    note: NoteHtmlResponse | null;
    slug: string;
    links: LinksResponse;
    onmoved: (folder: string) => void | Promise<void>;
  } = $props();

  let mode = $state<'content' | 'chunks'>('content');

  // Reset to the content view whenever the selected note changes.
  $effect(() => {
    void note?.note_id;
    mode = 'content';
  });

  const processed = $derived(note ? processHeadings(note.content_html) : { html: '', outline: [] });
</script>

<div class="preview">
  {#if note}
    <div class="preview__header">
      <span class="preview__path">{note.folder ? note.folder + '/' : ''}{note.title}</span>
      <NoteModeToggle {mode} onchange={(m) => (mode = m)} />
      <NoteActions {slug} noteId={note.note_id} folder={note.folder} variant="preview" {onmoved} />
    </div>
    <div class="preview__main">
      <div class="preview__body">
        <NoteBody {slug} noteId={note.note_id} html={processed.html} {mode} />
      </div>
      <NoteMeta
        {slug}
        tags={note.tags}
        outline={processed.outline}
        backlinks={links.backlinks}
        outlinks={links.outlinks}
        showOutline={mode === 'content'}
      />
    </div>
  {:else}
    <div class="preview__empty">
      <span>← wybierz notatkę</span>
    </div>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .preview {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 16px;
      border-bottom: 1px solid v.$border;
      flex-shrink: 0;
      gap: v.$space-sm;
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }

    &__main {
      display: flex;
      flex: 1;
      overflow: hidden;
    }

    &__body {
      padding: 16px;
      overflow-y: auto;
      flex: 1;
      font-size: 0.9rem;
      line-height: 1.6;
      color: v.$text-primary;
    }

    &__empty {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$text-muted;
    }
  }
</style>
```

- [ ] **Step 2: Weryfikacja**

Run: `bun run check && bun run test` (in `frontend/`). Expected: 0 errors; 22 tests pass.
Manual (opcjonalnie): explorer → wybierz notatkę: sidebar po prawej (Tagi/Outline/Backlinki/Wychodzące), toggle Treść/Chunki działa (chunki ładują się leniwie), outline scrolluje. `NoteLinksPanel` nie jest już używany w preview (usuniemy go w Task 6).

- [ ] **Step 3: Commit**

```bash
bunx prettier --write "src/routes/(protected)/workspace/[slug]/notes/[...path]/NotePreview.svelte"
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NotePreview.svelte"
git commit -m "feat: compose explorer preview from shared note components"
```

---

## Task 6: Złożenie pełnego widoku (`note/[id]`) + sprzątanie

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.ts`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte`
- Delete: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NoteLinksPanel.svelte`

- [ ] **Step 1: Load dorzuca `outlinks`**

In `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.ts`, change the final `return` to include outlinks:
```ts
  return { note: note.data, backlinks: links.data.backlinks, outlinks: links.data.outlinks };
```

- [ ] **Step 2: Przepisz pełny widok na klocki**

Replace the ENTIRE contents of `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte` with:
```svelte
<script lang="ts">
  import { invalidate, invalidateAll } from '$app/navigation';
  import { page } from '$app/state';
  import Breadcrumb from '$lib/components/Breadcrumb.svelte';
  import NoteActions from '$lib/components/note/NoteActions.svelte';
  import NoteBody from '$lib/components/note/NoteBody.svelte';
  import NoteMeta from '$lib/components/note/NoteMeta.svelte';
  import NoteModeToggle from '$lib/components/note/NoteModeToggle.svelte';
  import { processHeadings } from '$lib/outline';
  import { notesPath } from '$lib/routes';
  import { formatDate } from '$lib/utils/format';

  const slug = $derived(page.params.slug as string);
  const note = $derived(page.data.note);
  const backlinks = $derived(page.data.backlinks);
  const outlinks = $derived(page.data.outlinks);

  let mode = $state<'content' | 'chunks'>('content');
  const processed = $derived(processHeadings(note.content_html));

  async function handleMove() {
    await invalidate('app:workspace-tree');
    await invalidateAll();
  }
</script>

<main class="page">
  <Breadcrumb {slug} folder={note.folder} current={note.title} />
  <a href={notesPath(slug)} class="back-link">← Wróć do listy</a>

  <div class="note">
    <div class="note__doc">
      <header class="note__header">
        <p class="note__path">{slug}/{note.folder ? note.folder + '/' : ''}</p>
        <h1 class="note__title">{note.title}</h1>
        <div class="note__bar">
          <span class="note__date">Zaktualizowano: {formatDate(note.updated_at)}</span>
          <NoteModeToggle {mode} onchange={(m) => (mode = m)} />
          <NoteActions
            {slug}
            noteId={note.note_id}
            folder={note.folder}
            variant="full"
            onmoved={handleMove}
          />
        </div>
      </header>

      <NoteBody {slug} noteId={note.note_id} html={processed.html} {mode} />
    </div>

    <NoteMeta
      {slug}
      tags={note.tags}
      outline={processed.outline}
      {backlinks}
      {outlinks}
      showOutline={mode === 'content'}
    />
  </div>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 1000px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .back-link {
    display: inline-block;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    text-decoration: none;
    margin-bottom: v.$space-xl;
    transition: color 0.15s;
    &:hover {
      color: v.$accent;
    }
  }

  .note {
    display: flex;
    gap: v.$space-xl;
    align-items: flex-start;
  }

  .note__doc {
    flex: 1;
    min-width: 0;
  }

  .note__header {
    margin-bottom: v.$space-xl;
    padding-bottom: v.$space-lg;
    border-bottom: 1px solid v.$border;
  }

  .note__path {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
    margin: 0 0 v.$space-xs 0;
    letter-spacing: 0.03em;
  }

  .note__title {
    font-size: 1.75rem;
    font-family: v.$font-mono;
    color: v.$text-primary;
    margin: 0 0 v.$space-md 0;
    line-height: 1.3;
  }

  .note__bar {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    flex-wrap: wrap;
  }

  .note__date {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
  }
</style>
```

- [ ] **Step 3: Usuń martwy `NoteLinksPanel`**

```bash
git rm "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NoteLinksPanel.svelte"
```

- [ ] **Step 4: Weryfikacja**

Run: `bun run check && bun run test` (in `frontend/`). Expected: 0 errors; 22 tests pass. Brak odwołań do `NoteLinksPanel` (svelte-check by je wyłapał). Pełny widok ma teraz prawy sidebar (te same klocki co preview), toggle Treść/Chunki, tagi i backlinki w sidebarze (nie w headerze/na dole).

- [ ] **Step 5: Commit**

```bash
bunx prettier --write "src/routes/(protected)/workspace/[slug]/note/[id]/+page.ts" "src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte"
git add -A "frontend/src/routes/(protected)/workspace/[slug]/note/[id]/" "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NoteLinksPanel.svelte"
git commit -m "feat: compose full note view from shared components; remove NoteLinksPanel"
```

---

## Po planie

Notatka renderuje się z jednego zestawu klocków (`ChunkList`/`NoteMeta`/`NoteBody`/`NoteActions`/`NoteModeToggle`) w preview i pełnym widoku; sidebar (Tagi·Outline·Backlinki·Wychodzące) i podgląd chunków działają w obu; `/chunks` to wrapper; `outline` przetestowany. Zero zmian backendu.

**Dalej (osobne):** responsywność tablet/desktop tego układu → Visual Plan 2; dedup prymitywów → issue #13.
