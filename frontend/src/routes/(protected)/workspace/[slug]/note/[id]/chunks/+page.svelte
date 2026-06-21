<script lang="ts">
  import { notePath } from '$lib/routes';

  let { data } = $props();

  const preview = $derived(data.preview);
  const slug = $derived(data.slug);
  const noteId = $derived(data.noteId);
</script>

<main class="page">
  <a href={notePath(slug, noteId)} class="back-link">← Wróć do notatki</a>

  <header class="chunks-header">
    <h1 class="page-title">{preview.title}</h1>
    <div class="chunks-header__meta">
      <span class="badge" class:badge--stale={preview.index_state !== 'indexed'}>
        {preview.index_state}
      </span>
      <span class="chunks-header__count">{preview.chunk_count} chunków</span>
    </div>
  </header>

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

  .page-title {
    font-family: v.$font-mono;
    font-size: 1.25rem;
    color: v.$text-primary;
    margin: 0 0 v.$space-sm 0;
  }

  .chunks-header {
    margin-bottom: v.$space-xl;
    padding-bottom: v.$space-lg;
    border-bottom: 1px solid v.$border;

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
