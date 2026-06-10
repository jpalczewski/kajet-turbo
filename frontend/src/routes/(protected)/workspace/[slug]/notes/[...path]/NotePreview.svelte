<script lang="ts">
  import type { NoteHtmlResponse } from '$lib/api'

  let { note, slug }: {
    note: NoteHtmlResponse | null
    slug: string
  } = $props()
</script>

<div class="preview">
  {#if note}
    <div class="preview__header">
      <span class="preview__path">{note.folder ? note.folder + '/' : ''}{note.title}</span>
      <a
        href="/workspace/{slug}/note/{note.note_id}"
        class="preview__open-link"
        title="Otwórz pełny widok"
      >↗</a>
    </div>
    <div class="preview__body prose">
      <!-- eslint-disable-next-line svelte/no-at-html-tags -->
      {@html note.content_html}
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
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &__open-link {
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$accent-dark;
      text-decoration: none;
      flex-shrink: 0;
      margin-left: v.$space-sm;
      &:hover { color: v.$accent; }
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
