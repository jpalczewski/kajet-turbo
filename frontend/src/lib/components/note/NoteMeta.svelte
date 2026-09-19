<script lang="ts">
  import { goto } from '$app/navigation';
  import { browser } from '$app/environment';
  import { SvelteMap } from 'svelte/reactivity';
  import {
    apiNoteNeighborhoodApiWorkspacesNameNotesNoteIdNeighborhoodGet,
    type GraphResponse,
    type NoteLinkItem,
  } from '$lib/api';
  import GraphView from '$lib/components/GraphView.svelte';
  import RelatedNotesSection from '$lib/components/note/RelatedNotesSection.svelte';
  import type { AnyGraphNode } from '$lib/graph/model';
  import type { OutlineItem } from '$lib/outline';
  import { linkRelations } from '$lib/relatedNotes';
  import { RelatedNotesController } from '$lib/relatedNotes.svelte';
  import { noteInTreePath, tagsPath } from '$lib/routes';

  type GraphDepth = 1 | 2;

  let {
    slug,
    noteId,
    folder,
    tags,
    outline,
    backlinks,
    outlinks,
    showOutline = true,
  }: {
    slug: string;
    noteId: string;
    folder: string;
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

  const XWS_KEY = 'kajet:note-meta-xws';
  let showCrossWorkspace = $state(!(browser && localStorage.getItem(XWS_KEY) === '0'));

  function toggleXws() {
    showCrossWorkspace = !showCrossWorkspace;
    if (browser) localStorage.setItem(XWS_KEY, showCrossWorkspace ? '1' : '0');
  }

  const filteredBacklinks = $derived(
    showCrossWorkspace ? backlinks : backlinks.filter((l) => !l.workspace || l.workspace === slug),
  );
  const filteredOutlinks = $derived(
    showCrossWorkspace ? outlinks : outlinks.filter((l) => !l.workspace || l.workspace === slug),
  );
  const relationLists = $derived([
    { heading: 'Backlinki', links: filteredBacklinks },
    { heading: 'Wychodzące', links: filteredOutlinks },
  ]);

  // Lives here rather than inside the section so the request starts when the note is shown,
  // even while the whole rail is collapsed and the section is not rendered.
  const related = new RelatedNotesController(() => ({ slug, noteId, folder }));
  const relatedLinks = $derived(linkRelations(slug, backlinks, outlinks));

  let relationView = $state<'lists' | 'graph'>('lists');
  let graphDepth = $state<GraphDepth>(2);
  let graphData = $state<GraphResponse | null>(null);
  let graphLoading = $state(false);
  let graphError = $state('');
  let graphCache = new SvelteMap<string, GraphResponse>();
  let graphRequest = 0;

  function graphKey(depth: GraphDepth): string {
    return `${noteId}:${depth}:${showCrossWorkspace ? 'xws' : 'local'}`;
  }

  function setGraphDepth(value: number): void {
    graphDepth = value === 1 ? 1 : 2;
  }

  function handleGraphNodeClick(node: AnyGraphNode): void {
    if (node.kind !== 'note') return;
    goto(noteInTreePath(node.workspace ?? slug, node.folder, node.note_id));
  }

  // NotePreview keeps this component instance while switching notes. Drop all cached data and
  // invalidate an in-flight response so a previous note's graph can never leak into the new one.
  $effect(() => {
    void noteId;
    graphCache = new SvelteMap();
    graphData = null;
    graphError = '';
    graphLoading = false;
    graphRequest += 1;
  });

  // Fetch only when the graph view is opened. The cache key includes the xws setting because
  // the backend traversal scope changes with that existing control.
  $effect(() => {
    if (relationView !== 'graph') return;

    const key = graphKey(graphDepth);
    const cached = graphCache.get(key);
    if (cached) {
      graphRequest += 1;
      graphData = cached;
      graphLoading = false;
      graphError = '';
      return;
    }

    const request = ++graphRequest;
    graphData = null;
    graphLoading = true;
    graphError = '';
    apiNoteNeighborhoodApiWorkspacesNameNotesNoteIdNeighborhoodGet(slug, noteId, {
      depth: graphDepth,
      include_cross_workspace: showCrossWorkspace,
      include_tags: true,
    })
      .then((result) => {
        if (request !== graphRequest) return;
        if (result.status !== 200) {
          graphError = 'Nie udało się pobrać grafu.';
          return;
        }
        graphCache.set(key, result.data);
        graphData = result.data;
      })
      .catch(() => {
        if (request === graphRequest) graphError = 'Nie udało się pobrać grafu.';
      })
      .finally(() => {
        if (request === graphRequest) graphLoading = false;
      });
  });
</script>

{#if collapsed}
  <button class="rail" onclick={toggle} title="Pokaż panel" aria-label="Pokaż panel">
    <span class="rail__label">Info</span>
  </button>
{:else}
  <aside class="meta">
    <div class="meta__head">
      <span class="meta__title">Info</span>
      <div class="meta__head-controls">
        <label class="meta__xws-toggle" title="Pokaż/ukryj linki między workspace'ami">
          <input type="checkbox" bind:checked={showCrossWorkspace} onchange={toggleXws} />
          <span>xws</span>
        </label>
        <button class="meta__toggle" onclick={toggle} title="Zwiń" aria-label="Zwiń panel">»</button
        >
      </div>
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
            <li data-level={item.level}>
              <a class="meta__anchor" href={`#${item.id}`}>{item.text}</a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    <div class="meta__section meta__relation-switcher">
      <h4 class="meta__heading">Relacje</h4>
      <div class="meta__view-toggle" role="group" aria-label="Widok relacji">
        <button
          class:meta__view-toggle--active={relationView === 'lists'}
          aria-pressed={relationView === 'lists'}
          onclick={() => (relationView = 'lists')}>Listy</button
        >
        <button
          class:meta__view-toggle--active={relationView === 'graph'}
          aria-pressed={relationView === 'graph'}
          onclick={() => (relationView = 'graph')}>Graf</button
        >
      </div>
    </div>

    {#if relationView === 'graph'}
      <div class="meta__section meta__graph-section">
        <label class="meta__depth">
          <span>Głębokość</span>
          <select
            value={graphDepth}
            aria-label="Głębokość grafu"
            onchange={(event) =>
              setGraphDepth(Number((event.currentTarget as HTMLSelectElement).value))}
          >
            <option value="1">1 hop</option>
            <option value="2">2 hop-y</option>
          </select>
        </label>
        {#if graphLoading}
          <p class="meta__status">Ładowanie grafu…</p>
        {:else if graphError}
          <p class="meta__status meta__status--error">{graphError}</p>
        {:else if graphData}
          {#key graphKey(graphDepth)}
            <GraphView data={graphData} onNodeClick={handleGraphNodeClick} />
          {/key}
        {/if}
      </div>
    {:else}
      {#each relationLists as relation (relation.heading)}
        {#if relation.links.length > 0}
          <div class="meta__section">
            <h4 class="meta__heading">{relation.heading} ({relation.links.length})</h4>
            <ul class="meta__list">
              {#each relation.links as link (link.note_id)}
                <li>
                  <a
                    href={noteInTreePath(link.workspace ?? slug, link.folder, link.note_id)}
                    class="meta__link"
                  >
                    {#if link.workspace && link.workspace !== slug}
                      <span class="meta__xws">[{link.workspace}]</span>
                    {/if}
                    {#if link.folder}<span class="meta__folder">{link.folder}/</span
                      >{/if}{link.title}
                  </a>
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      {/each}
      <RelatedNotesSection
        {slug}
        phase={related.phase}
        status={related.status}
        items={related.items}
        scope={related.effectiveScope}
        canScopeToFolder={related.canScopeToFolder}
        relations={relatedLinks}
        onscope={(scope) => related.setScope(scope)}
        onretry={() => related.retry()}
      />
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

    &__head-controls {
      display: flex;
      align-items: center;
      gap: 4px;
    }

    &__xws-toggle {
      display: flex;
      align-items: center;
      gap: 3px;
      font-family: v.$font-mono;
      font-size: 0.65rem;
      color: v.$text-muted;
      cursor: pointer;
      user-select: none;
      input {
        cursor: pointer;
      }
      &:hover {
        color: v.$accent;
      }
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

    &__relation-switcher {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: v.$space-sm;
    }

    &__view-toggle {
      display: flex;
      gap: 2px;

      button {
        border: 1px solid v.$border;
        background: v.$bg-surface;
        color: v.$text-muted;
        cursor: pointer;
        font-family: v.$font-mono;
        font-size: 0.65rem;
        padding: 3px 5px;

        &:hover,
        &.meta__view-toggle--active {
          border-color: v.$border-accent;
          color: v.$text-primary;
        }
      }
    }

    &__graph-section {
      min-width: 0;
      height: 280px;
    }

    &__depth {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: v.$space-sm;
      margin-bottom: v.$space-sm;
      color: v.$text-muted;
      font-family: v.$font-mono;
      font-size: 0.68rem;

      select {
        border: 1px solid v.$border;
        background: v.$bg-surface;
        color: v.$text-secondary;
        font: inherit;
        padding: 2px 3px;
      }
    }

    &__status {
      color: v.$text-muted;
      font-family: v.$font-mono;
      font-size: 0.75rem;

      &--error {
        color: v.$error;
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

      // Indent by heading level (h1-h6). A CSS attribute selector, not an
      // inline style — CSP style-src has no per-value allowlist to give a
      // dynamic style="padding-left: {n}px", only a fixed set of hashes.
      > li {
        @for $level from 1 through 6 {
          &[data-level='#{$level}'] {
            padding-left: ($level - 1) * 10px;
          }
        }
      }
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

    &__xws {
      color: v.$accent-dark;
      font-size: 0.7em;
      margin-right: 3px;
    }
  }
</style>
