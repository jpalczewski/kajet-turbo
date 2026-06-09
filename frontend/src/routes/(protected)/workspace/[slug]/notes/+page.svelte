<script lang="ts">
  import { page } from '$app/state'
  import type { NoteItem } from '$lib/types'

  const slug = $derived(page.params.slug)
  const notes: NoteItem[] = $derived(page.data.notes ?? [])

  function formatDate(iso: string): string {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' })
  }
</script>

<main class="page">
  <header class="page__header">
    <h1>{slug}</h1>
    <span class="page__count">{notes.length}</span>
  </header>

  {#if notes.length === 0}
    <p class="empty">Brak notatek w tym workspace.</p>
  {:else}
    <ul class="notes-list">
      {#each notes as note}
        <li class="note-card">
          <a href="/workspace/{slug}/note/{note.note_id}" class="note-card__link">
            <div class="note-card__main">
              {#if note.folder}
                <span class="note-card__folder">{note.folder}/</span>
              {/if}
              <span class="note-card__title">{note.title}</span>
              {#if note.tags.length > 0}
                <div class="note-card__tags">
                  {#each note.tags as tag}
                    <span class="note-card__tag">#{tag}</span>
                  {/each}
                </div>
              {/if}
            </div>
            <span class="note-card__date">{formatDate(note.updated_at)}</span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 720px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .page__header {
    display: flex;
    align-items: baseline;
    gap: v.$space-md;
    margin-bottom: v.$space-xl;

    h1 {
      font-size: 1.5rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-primary;
      margin: 0;
    }
  }

  .page__count {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$accent-dark;
    background: rgba(240, 184, 0, 0.08);
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    padding: 2px 8px;
  }

  .empty {
    font-size: 0.85rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .notes-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;
  }

  .note-card {
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    transition: border-color 0.15s;

    &:hover { border-color: v.$accent-dark; }

    &__link {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: v.$space-md;
      padding: v.$space-md v.$space-lg;
      text-decoration: none;
    }

    &__main {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }

    &__folder {
      font-size: 0.7rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      letter-spacing: 0.03em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__title {
      font-size: 0.9rem;
      font-family: v.$font-mono;
      color: v.$text-primary;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__tags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    &__tag {
      font-size: 0.7rem;
      font-family: v.$font-mono;
      color: v.$accent-dark;
      letter-spacing: 0.04em;
    }

    &__date {
      font-size: 0.75rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      white-space: nowrap;
      flex-shrink: 0;
    }
  }
</style>
