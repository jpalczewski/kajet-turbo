<script lang="ts">
  import { goto } from '$app/navigation'
  import type { NoteItem } from '$lib/api'

  let { notes, currentNoteId, folderPath, slug }: {
    notes: NoteItem[]
    currentNoteId: string | null
    folderPath: string
    slug: string
  } = $props()

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  function formatDate(iso: string): string {
    if (!iso) return ''
    return new Date(iso).toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' })
  }

  function openNote(noteId: string) {
    const base = folderPath ? `/workspace/${slug}/notes/${folderPath}` : `/workspace/${slug}/notes`
    goto(`${base}/${noteId}`)
  }
</script>

<div class="notes-list">
  <div class="notes-list__header">
    <span class="notes-list__path">{folderPath || slug}/</span>
    <span class="notes-list__count">{notes.length}</span>
  </div>

  {#if notes.length === 0}
    <p class="notes-list__empty">Brak notatek.</p>
  {:else}
    <ul>
      {#each notes as note}
        <li>
          <button
            class="note-row"
            class:active={note.note_id === currentNoteId}
            onclick={() => openNote(note.note_id)}
          >
            <span class="note-row__title">{note.title}</span>
            <span class="note-row__meta">
              <span class="note-row__size">{formatSize(note.size_bytes)}</span>
              <span class="note-row__date">{formatDate(note.updated_at)}</span>
            </span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .notes-list {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    border-left: 1px solid v.$border;
    border-right: 1px solid v.$border;

    &__header {
      display: flex;
      align-items: center;
      gap: v.$space-sm;
      padding: 8px 12px;
      border-bottom: 1px solid v.$border;
      flex-shrink: 0;
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      letter-spacing: 0.03em;
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
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$text-muted;
      padding: 16px 12px;
    }

    ul {
      list-style: none;
      padding: 0;
      margin: 0;
      overflow-y: auto;
      flex: 1;
    }
  }

  .note-row {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 100%;
    padding: 7px 12px;
    background: none;
    border: none;
    border-bottom: 1px solid v.$border;
    cursor: pointer;
    text-align: left;

    &:hover { background: rgba(255,255,255,0.02); }
    &.active { background: rgba(240, 184, 0, 0.06); }

    &__title {
      font-family: v.$font-mono;
      font-size: 0.85rem;
      color: v.$text-primary;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__meta {
      display: flex;
      gap: v.$space-sm;
    }

    &__size,
    &__date {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$text-muted;
    }
  }
</style>
