<script lang="ts">
  import type { NoteHistoryEntry } from '$lib/api';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
  import { formatUnixDateTime } from '$lib/utils/format';

  let {
    entries,
    selectedSha,
    onselect,
  }: {
    entries: NoteHistoryEntry[];
    selectedSha: string | null;
    onselect: (sha: string) => void;
  } = $props();
</script>

<aside class="history-list">
  {#if entries.length === 0}
    <EmptyState>Brak historii.</EmptyState>
  {/if}
  {#each entries as entry (entry.sha)}
    <button
      class="history-entry"
      class:history-entry--active={selectedSha === entry.sha}
      onclick={() => onselect(entry.sha)}
    >
      <span class="history-entry__date">{formatUnixDateTime(entry.timestamp)}</span>
      <span class="history-entry__msg">{entry.message}</span>
    </button>
  {/each}
</aside>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .history-list {
    border-right: 1px solid v.$border;
    padding-right: v.$space-lg;
    display: flex;
    flex-direction: column;
    gap: v.$space-xs;
  }

  .history-entry {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: v.$space-sm v.$space-md;
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    background: none;
    cursor: pointer;
    text-align: left;
    transition:
      border-color 0.15s,
      background 0.15s;

    &:hover {
      border-color: v.$accent-dark;
    }

    &--active {
      border-color: v.$accent;
      background: v.$bg-raised;
    }

    &__date {
      font-size: 0.7rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }

    &__msg {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
</style>
