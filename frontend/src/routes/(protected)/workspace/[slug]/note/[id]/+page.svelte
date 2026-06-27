<script lang="ts">
  import { goto, invalidate, invalidateAll } from '$app/navigation';
  import { page } from '$app/state';
  import Breadcrumb from '$lib/components/Breadcrumb.svelte';
  import NoteActions from '$lib/components/note/NoteActions.svelte';
  import NoteBody from '$lib/components/note/NoteBody.svelte';
  import NoteMeta from '$lib/components/note/NoteMeta.svelte';
  import NoteModeToggle from '$lib/components/note/NoteModeToggle.svelte';
  import { processHeadings } from '$lib/outline';
  import { notesPath } from '$lib/routes';
  import { formatDate } from '$lib/utils/format';

  const slug = $derived(page.params.slug as string);
  const note = $derived(page.data.note);
  const backlinks = $derived(page.data.backlinks);
  const outlinks = $derived(page.data.outlinks);

  let mode = $state<'content' | 'chunks'>('content');
  const processed = $derived(processHeadings(note.content_html));

  async function handleMove() {
    await invalidate('app:workspace-tree');
    await invalidateAll();
  }

  function handleDelete(): void {
    goto(notesPath(slug, note.folder ?? ''));
  }
</script>

<main class="page">
  <Breadcrumb {slug} folder={note.folder} current={note.title} />
  <a href={notesPath(slug)} class="back-link">← Wróć do listy</a>

  <div class="note">
    <div class="note__doc">
      <header class="note__header">
        <p class="note__path">{slug}/{note.folder ? note.folder + '/' : ''}</p>
        <h1 class="note__title">{note.title}</h1>
        <div class="note__bar">
          <span class="note__date">Zaktualizowano: {formatDate(note.updated_at)}</span>
          <NoteModeToggle {mode} onchange={(m) => (mode = m)} />
          <NoteActions
            {slug}
            noteId={note.note_id}
            folder={note.folder}
            noteTitle={note.title}
            variant="full"
            onmoved={handleMove}
            ondeleted={handleDelete}
          />
        </div>
      </header>

      <NoteBody {slug} noteId={note.note_id} html={processed.html} {mode} />
    </div>

    <NoteMeta
      {slug}
      tags={note.tags}
      outline={processed.outline}
      {backlinks}
      {outlinks}
      showOutline={mode === 'content'}
    />
  </div>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 1000px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .back-link {
    display: inline-block;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    text-decoration: none;
    margin-bottom: v.$space-xl;
    transition: color 0.15s;
    &:hover {
      color: v.$accent;
    }
  }

  .note {
    display: flex;
    gap: v.$space-xl;
    align-items: flex-start;
  }

  .note__doc {
    flex: 1;
    min-width: 0;
  }

  .note__header {
    margin-bottom: v.$space-xl;
    padding-bottom: v.$space-lg;
    border-bottom: 1px solid v.$border;
  }

  .note__path {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
    margin: 0 0 v.$space-xs 0;
    letter-spacing: 0.03em;
  }

  .note__title {
    font-size: 1.75rem;
    font-family: v.$font-mono;
    color: v.$text-primary;
    margin: 0 0 v.$space-md 0;
    line-height: 1.3;
  }

  .note__bar {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    flex-wrap: wrap;
  }

  .note__date {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
  }
</style>
