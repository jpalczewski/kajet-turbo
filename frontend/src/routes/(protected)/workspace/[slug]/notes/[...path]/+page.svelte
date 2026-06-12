<script lang="ts">
  import { invalidate, goto } from '$app/navigation';
  import {
    apiCreateFolderApiWorkspacesNameFoldersPost,
    apiCreateNoteApiWorkspacesNameNotesPost,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import { noteEditPath, notesPath } from '$lib/routes';
  import FolderTree from './FolderTree.svelte';
  import NotesList from './NotesList.svelte';
  import NotePreview from './NotePreview.svelte';

  let { data } = $props();
  let slug = $derived(data.slug);

  async function handleCreateFolder(path: string): Promise<void> {
    try {
      await apiCreateFolderApiWorkspacesNameFoldersPost(slug, jsonBody({ path }));
    } catch (e) {
      throw new Error(apiErrorMessage(e, 'Nie udało się utworzyć folderu'), { cause: e });
    }
    await invalidate('app:workspace-tree');
    goto(notesPath(slug, path));
  }

  async function handleCreateNote(title: string): Promise<void> {
    let noteId: string;
    try {
      const result = await apiCreateNoteApiWorkspacesNameNotesPost(
        slug,
        jsonBody({ title, folder: data.folderPath, content: '' }),
      );
      if (result.status !== 201) throw new Error();
      noteId = result.data.note_id;
    } catch (e) {
      throw new Error(apiErrorMessage(e, 'Nie udało się utworzyć notatki'), { cause: e });
    }
    await invalidate('app:workspace-tree');
    goto(noteEditPath(slug, noteId));
  }
</script>

<div class="explorer">
  <aside class="explorer__sidebar">
    <FolderTree
      folders={data.tree.folders}
      currentFolder={data.folderPath}
      {slug}
      onCreateFolder={handleCreateFolder}
    />
  </aside>

  <section class="explorer__list">
    <NotesList
      notes={data.notes}
      currentNoteId={data.noteId}
      folderPath={data.folderPath}
      {slug}
      onCreateNote={handleCreateNote}
    />
  </section>

  <section class="explorer__preview">
    <NotePreview note={data.note} {slug} />
  </section>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .explorer {
    display: grid;
    grid-template-columns: 200px 280px 1fr;
    height: calc(100vh - 48px);
    overflow: hidden;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    margin: v.$space-lg;
    background: v.$bg-surface;

    &__sidebar {
      background: v.$bg-deep;
      border-right: 1px solid v.$border;
      overflow: hidden;
      padding-top: v.$space-sm;
    }

    &__list {
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    &__preview {
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
  }
</style>
