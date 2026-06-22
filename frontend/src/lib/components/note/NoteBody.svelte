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

  // Drop the cached chunks when the note changes, so a different note never
  // shows the previously loaded note's chunks (the component instance is reused).
  $effect(() => {
    void noteId;
    chunks = null;
    error = '';
  });

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
