<script lang="ts">
  import { page } from '$app/state'

  const slug = $derived(page.params.slug)
  const note = $derived(page.data.note)

  function formatDate(iso: string): string {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' })
  }
</script>

<main class="page">
  <nav class="breadcrumb">
    <a href="/workspaces" class="breadcrumb__link">Workspaces</a>
    <span class="breadcrumb__sep">/</span>
    <a href="/workspace/{slug}/notes" class="breadcrumb__link">{slug}</a>
    <span class="breadcrumb__sep">/</span>
    <span class="breadcrumb__current">{note.title}</span>
  </nav>

  <a href="/workspace/{slug}/notes" class="back-link">← Wróć do listy</a>

  <header class="note-header">
    <h1 class="note-header__title">{note.title}</h1>
    {#if note.tags.length > 0}
      <div class="note-header__tags">
        {#each note.tags as tag}
          <span class="note-header__tag">#{tag}</span>
        {/each}
      </div>
    {/if}
    <p class="note-header__date">Zaktualizowano: {formatDate(note.updated_at)}</p>
  </header>

  <div class="prose">
    {@html note.content_html}
  </div>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .breadcrumb {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    margin-bottom: v.$space-lg;
    font-size: 0.75rem;
    font-family: v.$font-mono;

    &__link {
      color: v.$accent-dark;
      text-decoration: none;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      transition: color 0.15s;

      &:hover { color: v.$accent; }
    }

    &__sep { color: v.$text-muted; }

    &__current {
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 200px;
    }
  }

  .back-link {
    display: inline-block;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    text-decoration: none;
    margin-bottom: v.$space-xl;
    transition: color 0.15s;

    &:hover { color: v.$accent; }
  }

  .note-header {
    margin-bottom: v.$space-xl;
    padding-bottom: v.$space-lg;
    border-bottom: 1px solid v.$border;

    &__title {
      font-size: 1.75rem;
      font-family: v.$font-mono;
      color: v.$text-primary;
      margin: 0 0 v.$space-md 0;
      line-height: 1.3;
    }

    &__tags {
      display: flex;
      flex-wrap: wrap;
      gap: v.$space-sm;
      margin-bottom: v.$space-sm;
    }

    &__tag {
      font-size: 0.75rem;
      font-family: v.$font-mono;
      color: v.$accent-dark;
      letter-spacing: 0.04em;
    }

    &__date {
      font-size: 0.75rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      margin: 0;
    }
  }

  // .prose is a real Svelte element (has scoped hash).
  // :global(el) inside it compiles to .prose[hash] el — matches injected HTML children.
  .prose {
    color: v.$text-primary;
    font-size: 0.95rem;
    line-height: 1.7;

    :global(h1),
    :global(h2),
    :global(h3),
    :global(h4) {
      font-family: v.$font-mono;
      color: v.$text-primary;
      margin: v.$space-xl 0 v.$space-md 0;
      line-height: 1.3;
    }

    :global(h1) { font-size: 1.5rem; }
    :global(h2) { font-size: 1.25rem; }
    :global(h3) { font-size: 1.05rem; }

    :global(p) { margin: 0 0 v.$space-md 0; }

    :global(a) {
      color: v.$accent;
      text-decoration: underline;
      text-underline-offset: 3px;
    }

    :global(a:hover) { color: v.$accent-hover; }

    :global(ul),
    :global(ol) {
      padding-left: v.$space-lg;
      margin: 0 0 v.$space-md 0;
    }

    :global(li) { margin-bottom: v.$space-xs; }

    :global(code) {
      font-family: v.$font-mono;
      font-size: 0.85em;
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 5px;
      color: v.$accent-light;
    }

    :global(pre) {
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      padding: v.$space-md;
      overflow-x: auto;
      margin: 0 0 v.$space-md 0;
    }

    :global(pre code) {
      background: none;
      border: none;
      padding: 0;
      color: v.$text-primary;
      font-size: 0.85rem;
    }

    :global(blockquote) {
      margin: 0 0 v.$space-md 0;
      padding: v.$space-sm v.$space-md;
      border-left: 3px solid v.$accent-dark;
      background: v.$bg-raised;
      color: v.$text-secondary;
      font-style: italic;
    }

    :global(blockquote p) { margin: 0; }

    :global(hr) {
      border: none;
      border-top: 1px solid v.$border;
      margin: v.$space-xl 0;
    }

    :global(table) {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: v.$space-md;
      font-family: v.$font-mono;
      font-size: 0.85rem;
    }

    :global(th),
    :global(td) {
      padding: v.$space-sm v.$space-md;
      border: 1px solid v.$border;
      text-align: left;
    }

    :global(th) {
      background: v.$bg-raised;
      color: v.$text-secondary;
    }
  }
</style>
