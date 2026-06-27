<script lang="ts">
  import type { LinksResponse, NoteHtmlResponse } from '$lib/api';
  import NoteActions from '$lib/components/note/NoteActions.svelte';
  import NoteBody from '$lib/components/note/NoteBody.svelte';
  import NoteMeta from '$lib/components/note/NoteMeta.svelte';
  import NoteModeToggle from '$lib/components/note/NoteModeToggle.svelte';
  import { processHeadings } from '$lib/outline';

  let {
    note,
    slug,
    links,
    onmoved,
    ondeleted,
  }: {
    note: NoteHtmlResponse | null;
    slug: string;
    links: LinksResponse;
    onmoved: (folder: string) => void | Promise<void>;
    ondeleted: () => void | Promise<void>;
  } = $props();

  let mode = $state<'content' | 'chunks'>('content');

  // Reset to the content view whenever the selected note changes.
  $effect(() => {
    void note?.note_id;
    mode = 'content';
  });

  const processed = $derived(note ? processHeadings(note.content_html) : { html: '', outline: [] });
</script>

<div class="preview">
  {#if note}
    <div class="preview__header">
      <span class="preview__path">{note.folder ? note.folder + '/' : ''}{note.title}</span>
      <NoteModeToggle {mode} onchange={(m) => (mode = m)} />
      <NoteActions
        {slug}
        noteId={note.note_id}
        folder={note.folder}
        noteTitle={note.title}
        variant="preview"
        {onmoved}
        {ondeleted}
      />
    </div>
    <div class="preview__main">
      <div class="preview__body">
        <NoteBody {slug} noteId={note.note_id} html={processed.html} {mode} />
      </div>
      <NoteMeta
        {slug}
        tags={note.tags}
        outline={processed.outline}
        backlinks={links.backlinks}
        outlinks={links.outlinks}
        showOutline={mode === 'content'}
      />
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

    &__main {
      display: flex;
      flex: 1;
      overflow: hidden;
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
