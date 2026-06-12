<script lang="ts">
  import type { NoteHtmlResponse } from '$lib/api';
  import Prose from '$lib/components/Prose.svelte';
  import { noteHistoryPath, notePath } from '$lib/routes';

  let {
    note,
    slug,
  }: {
    note: NoteHtmlResponse | null;
    slug: string;
  } = $props();
</script>

<div class="preview">
  {#if note}
    <div class="preview__header">
      <span class="preview__path">{note.folder ? note.folder + '/' : ''}{note.title}</span>
      <div class="preview__actions">
        <a href={noteHistoryPath(slug, note.note_id)} class="preview__action-link" title="Historia"
          >Historia</a
        >
        <a
          href={notePath(slug, note.note_id)}
          class="preview__action-link preview__action-link--primary"
          title="Otwórz pełny widok">↗</a
        >
      </div>
    </div>
    <div class="preview__body">
      <Prose html={note.content_html} />
    </div>
  {:else}
    <div class="preview__empty">
      <span>← wybierz notatkę</span>
    </div>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .preview {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 16px;
      border-bottom: 1px solid v.$border;
      flex-shrink: 0;
      gap: v.$space-sm;
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }

    &__actions {
      display: flex;
      align-items: center;
      gap: v.$space-sm;
      flex-shrink: 0;
    }

    &__action-link {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      text-decoration: none;
      white-space: nowrap;
      &:hover {
        color: v.$text-primary;
      }

      &--primary {
        color: v.$accent-dark;
        font-size: 0.8rem;
        &:hover {
          color: v.$accent;
        }
      }
    }

    &__body {
      padding: 16px;
      overflow-y: auto;
      flex: 1;
      font-size: 0.9rem;
      line-height: 1.6;
      color: v.$text-primary;
    }

    &__empty {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$text-muted;
    }
  }
</style>
