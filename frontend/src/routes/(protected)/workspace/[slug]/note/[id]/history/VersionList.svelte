<script lang="ts">
  import type { NoteHistoryEntry } from '$lib/api';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
  import ListRow from '$lib/components/ui/ListRow.svelte';
  import { formatUnixDateTime, type DateFormatPrefs } from '$lib/utils/format';

  let {
    entries,
    selectedSha,
    onselect,
    datePrefs,
  }: {
    entries: NoteHistoryEntry[];
    selectedSha: string | null;
    onselect: (sha: string) => void;
    datePrefs: DateFormatPrefs;
  } = $props();
</script>

<aside class="history-list">
  {#if entries.length === 0}
    <EmptyState>Brak historii.</EmptyState>
  {/if}
  {#each entries as entry (entry.sha)}
    <ListRow variant="card" active={selectedSha === entry.sha} onclick={() => onselect(entry.sha)}>
      <span class="history-date">{formatUnixDateTime(entry.timestamp, datePrefs)}</span>
      <span class="history-msg">{entry.message}</span>
    </ListRow>
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

  .history-date {
    font-size: 0.7rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
  }

  .history-msg {
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
