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
            <li style="padding-left: {(item.level - 1) * 10}px">
              <a class="meta__anchor" href={`#${item.id}`}>{item.text}</a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    {#if filteredBacklinks.length > 0}
      <div class="meta__section">
        <h4 class="meta__heading">Backlinki ({filteredBacklinks.length})</h4>
        <ul class="meta__list">
          {#each filteredBacklinks as link (link.note_id)}
            <li>
              <a
                href={noteInTreePath(link.workspace ?? slug, link.folder, link.note_id)}
                class="meta__link"
              >
                {#if link.workspace && link.workspace !== slug}
                  <span class="meta__xws">[{link.workspace}]</span>
                {/if}
                {#if link.folder}<span class="meta__folder">{link.folder}/</span>{/if}{link.title}
              </a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    {#if filteredOutlinks.length > 0}
      <div class="meta__section">
        <h4 class="meta__heading">Wychodzące ({filteredOutlinks.length})</h4>
        <ul class="meta__list">
          {#each filteredOutlinks as link (link.note_id)}
            <li>
              <a
                href={noteInTreePath(link.workspace ?? slug, link.folder, link.note_id)}
                class="meta__link"
              >
                {#if link.workspace && link.workspace !== slug}
                  <span class="meta__xws">[{link.workspace}]</span>
                {/if}
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

    &__xws {
      color: v.$accent-dark;
      font-size: 0.7em;
      margin-right: 3px;
    }
  }
</style>
