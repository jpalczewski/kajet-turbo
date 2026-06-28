<script lang="ts">
  import type { ChunkPreviewResponse } from '$lib/api';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';

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
  <EmptyState>Brak chunków (pusta notatka).</EmptyState>
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
