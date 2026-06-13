<script lang="ts">
  import { browser } from '$app/environment';
  import type { NoteLinkItem } from '$lib/api';
  import { noteInTreePath } from '$lib/routes';

  let {
    slug,
    backlinks,
    outlinks,
  }: {
    slug: string;
    backlinks: NoteLinkItem[];
    outlinks: NoteLinkItem[];
  } = $props();

  const STORAGE_KEY = 'kajet:links-panel-collapsed';

  let collapsed = $state(browser && localStorage.getItem(STORAGE_KEY) === '1');

  function toggle() {
    collapsed = !collapsed;
    if (browser) localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
  }
</script>

{#if collapsed}
  <button class="rail" onclick={toggle} title="Pokaż linki" aria-label="Pokaż linki">
    <span class="rail__label">Linki</span>
  </button>
{:else}
  <aside class="panel">
    <div class="panel__head">
      <span class="panel__title">Linki</span>
      <button class="panel__toggle" onclick={toggle} title="Zwiń" aria-label="Zwiń panel linków"
        >«</button
      >
    </div>

    <div class="panel__section">
      <h4 class="panel__heading">Wychodzące ({outlinks.length})</h4>
      {#if outlinks.length > 0}
        <ul class="panel__list">
          {#each outlinks as link (link.note_id)}
            <li>
              <a href={noteInTreePath(slug, link.folder, link.note_id)} class="panel__link">
                {#if link.folder}<span class="panel__folder">{link.folder}/</span>{/if}{link.title}
              </a>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="panel__empty">Brak</p>
      {/if}
    </div>

    <div class="panel__section">
      <h4 class="panel__heading">Backlinki ({backlinks.length})</h4>
      {#if backlinks.length > 0}
        <ul class="panel__list">
          {#each backlinks as link (link.note_id)}
            <li>
              <a href={noteInTreePath(slug, link.folder, link.note_id)} class="panel__link">
                {#if link.folder}<span class="panel__folder">{link.folder}/</span>{/if}{link.title}
              </a>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="panel__empty">Brak</p>
      {/if}
    </div>
  </aside>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .rail {
    flex-shrink: 0;
    width: 28px;
    border: none;
    border-right: 1px solid v.$border;
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
      transform: rotate(180deg);
      font-family: v.$font-mono;
      font-size: 0.7rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
  }

  .panel {
    flex-shrink: 0;
    width: 200px;
    border-right: 1px solid v.$border;
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
      color: v.$accent;
      text-decoration: none;
      word-break: break-word;

      &:hover {
        color: v.$accent-hover;
        text-decoration: underline;
      }
    }

    &__folder {
      color: v.$text-muted;
    }

    &__empty {
      margin: 0;
      font-family: v.$font-mono;
      font-size: 0.75rem;
      color: v.$text-muted;
    }
  }
</style>
