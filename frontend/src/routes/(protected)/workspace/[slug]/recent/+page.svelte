<script lang="ts">
  import { onMount } from 'svelte';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
  import ListRow from '$lib/components/ui/ListRow.svelte';
  import { notePath } from '$lib/routes';
  import { DEFAULT_DATE_PREFS, formatDateTime, formatRelative } from '$lib/utils/format';

  let { data } = $props();
  const datePrefs = $derived(data.session?.preferences ?? DEFAULT_DATE_PREFS);

  // Relative labels go stale on their own; a tick re-renders them between edits.
  let now = $state(Date.now());
  onMount(() => {
    const timer = setInterval(() => (now = Date.now()), 60_000);
    return () => clearInterval(timer);
  });
</script>

<div class="recent">
  <header class="recent__header">
    <h1 class="recent__heading">Ostatnio edytowane</h1>
    <span class="recent__count">{data.notes.length}</span>
  </header>

  {#if data.notes.length === 0}
    <div class="recent__empty">
      <EmptyState>Brak notatek.</EmptyState>
    </div>
  {:else}
    <ul>
      {#each data.notes as note (note.note_id)}
        <li>
          <ListRow href={notePath(data.slug, note.note_id)} layout="inline">
            <span class="note-title">{note.title}</span>
            {#if note.folder}
              <span class="note-folder">{note.folder}</span>
            {/if}
            <time
              class="note-time"
              datetime={note.updated_at}
              title={formatDateTime(note.updated_at, datePrefs)}
            >
              {formatRelative(note.updated_at, datePrefs, now)}
            </time>
          </ListRow>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .recent {
    display: flex;
    flex-direction: column;
    height: calc(100dvh - 48px);
    overflow: hidden;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    margin: v.$space-lg;
    background: v.$bg-surface;

    ul {
      list-style: none;
      margin: 0;
      padding: 0;
      overflow-y: auto;
      flex: 1;
    }

    &__header {
      display: flex;
      align-items: baseline;
      gap: v.$space-sm;
      padding: 8px 12px;
      border-bottom: 1px solid v.$border;
      background: v.$bg-deep;
      flex-shrink: 0;
    }

    &__heading {
      margin: 0;
      font-family: v.$font-mono;
      font-size: 0.72rem;
      font-weight: 400;
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    &__count {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$accent-dark;
      background: rgba(240, 184, 0, 0.08);
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 6px;
    }

    &__empty {
      padding: v.$space-lg;
    }
  }

  .note-title {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: v.$font-mono;
    font-size: 0.85rem;
    color: v.$text-primary;
  }

  .note-folder {
    flex: 1 1 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$text-muted;
  }

  .note-time {
    flex-shrink: 0;
    margin-left: auto;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$accent-dark;
  }

  @include bp.mobile {
    .recent {
      height: auto;
      margin: 0;
      border: none;
      border-radius: 0;
    }
  }
</style>
