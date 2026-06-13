<script lang="ts">
  import { invalidate, invalidateAll } from '$app/navigation';
  import { page } from '$app/state';
  import Breadcrumb from '$lib/components/Breadcrumb.svelte';
  import MoveNoteDialog from '$lib/components/MoveNoteDialog.svelte';
  import Prose from '$lib/components/Prose.svelte';
  import { noteEditPath, noteHistoryPath, notePath, notesPath } from '$lib/routes';
  import { formatDate } from '$lib/utils/format';

  const slug = $derived(page.params.slug as string);
  const note = $derived(page.data.note);
  const backlinks = $derived(page.data.backlinks);

  async function handleMove() {
    await invalidate('app:workspace-tree');
    await invalidateAll();
  }
</script>

<main class="page">
  <Breadcrumb {slug} folder={note.folder} current={note.title} />

  <a href={notesPath(slug)} class="back-link">← Wróć do listy</a>

  <header class="note-header">
    <p class="note-header__path">{slug}/{note.folder ? note.folder + '/' : ''}</p>
    <h1 class="note-header__title">{note.title}</h1>
    {#if note.tags.length > 0}
      <div class="note-header__tags">
        {#each note.tags as tag (tag)}
          <span class="note-header__tag">#{tag}</span>
        {/each}
      </div>
    {/if}
    <p class="note-header__date">
      Zaktualizowano: {formatDate(note.updated_at)} ·
      <a href={noteHistoryPath(slug, note.note_id)} class="note-header__history-link">Historia</a>
      ·
      <a href={noteEditPath(slug, note.note_id)} class="note-header__history-link">Edytuj</a>
      ·
      <MoveNoteDialog
        {slug}
        noteId={note.note_id}
        currentFolder={note.folder}
        onmoved={handleMove}
      />
    </p>
  </header>

  <Prose html={note.content_html} />

  {#if backlinks.length > 0}
    <section class="backlinks">
      <h2 class="backlinks__title">Linkują tutaj ({backlinks.length})</h2>
      <ul class="backlinks__list">
        {#each backlinks as backlink (backlink.note_id)}
          <li>
            <a href={notePath(slug, backlink.note_id)} class="backlinks__link">
              {#if backlink.folder}<span class="backlinks__folder">{backlink.folder}/</span
                >{/if}{backlink.title}
            </a>
          </li>
        {/each}
      </ul>
    </section>
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 800px;
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

  .backlinks {
    margin-top: v.$space-2xl;
    padding-top: v.$space-lg;
    border-top: 1px solid v.$border;

    &__title {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin: 0 0 v.$space-md 0;
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: v.$space-xs;
    }

    &__link {
      font-family: v.$font-mono;
      font-size: 0.85rem;
      color: v.$accent;
      text-decoration: none;

      &:hover {
        color: v.$accent-hover;
        text-decoration: underline;
      }
    }

    &__folder {
      color: v.$text-muted;
    }
  }

  .note-header {
    margin-bottom: v.$space-xl;
    padding-bottom: v.$space-lg;
    border-bottom: 1px solid v.$border;

    &__path {
      font-size: 0.75rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      margin: 0 0 v.$space-xs 0;
      letter-spacing: 0.03em;
    }

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

    &__history-link {
      color: v.$accent-dark;
      text-decoration: none;
      font-size: 0.75rem;
      font-family: v.$font-mono;
      letter-spacing: 0.04em;
      transition: color 0.15s;

      &:hover {
        color: v.$accent;
      }
    }
  }
</style>
