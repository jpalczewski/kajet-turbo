<script lang="ts">
  import { invalidate, goto } from '$app/navigation';
  import {
    apiCreateFolderApiWorkspacesNameFoldersPost,
    apiCreateNoteApiWorkspacesNameNotesPost,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import {
    noteEditPath,
    noteInTreePath,
    notesPath,
    tagsPath,
    workspaceSettingsPath,
  } from '$lib/routes';
  import { activePane } from '$lib/explorerView';
  import ExplorerModeToggle from './ExplorerModeToggle.svelte';
  import FolderTree from './FolderTree.svelte';
  import TagTree from './TagTree.svelte';
  import NotesList from './NotesList.svelte';
  import NotePreview from './NotePreview.svelte';
  import MobileFolderNav from './MobileFolderNav.svelte';

  let { data } = $props();
  let slug = $derived(data.slug);
  let pane = $derived(activePane({ noteSelected: data.noteSelected }));

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

  async function handleMoveNote(folder: string): Promise<void> {
    if (!data.noteId) return;
    await invalidate('app:workspace-tree');
    goto(noteInTreePath(slug, folder, data.noteId));
  }

  function toggleDescendants() {
    // eslint-disable-next-line svelte/no-navigation-without-resolve
    goto(tagsPath(slug, data.tagPath, !data.includeDescendants));
  }
</script>

<div class="explorer" class:explorer--preview={pane === 'preview'}>
  <aside class="explorer__sidebar">
    <ExplorerModeToggle {slug} mode={data.mode} />
    <div class="explorer__tree">
      {#if data.mode === 'tags'}
        <TagTree
          tags={data.tags}
          currentTag={data.tagPath}
          includeDescendants={data.includeDescendants}
          {slug}
        />
      {:else}
        <FolderTree
          folders={data.tree.folders}
          currentFolder={data.folderPath}
          {slug}
          onCreateFolder={handleCreateFolder}
        />
      {/if}
    </div>
    <a class="explorer__settings" href={workspaceSettingsPath(slug)}>⚙ Ustawienia</a>
  </aside>

  <section class="explorer__list">
    <MobileFolderNav
      {slug}
      mode={data.mode}
      folderPath={data.folderPath}
      folders={data.tree.folders}
      tags={data.tags}
      currentTag={data.tagPath}
      includeDescendants={data.includeDescendants}
    />
    {#if data.mode === 'tags'}
      <div class="tag-list-header">
        <span class="tag-list-title">{data.tagPath ? '#' + data.tagPath : 'Wybierz tag'}</span>
        {#if data.tagPath}
          <label class="desc-toggle">
            <input type="checkbox" checked={data.includeDescendants} onchange={toggleDescendants} />
            z podtagami
          </label>
        {/if}
      </div>
      <NotesList
        notes={data.notes}
        currentNoteId={data.noteId}
        folderPath=""
        {slug}
        onCreateNote={handleCreateNote}
        useNoteFolder
        showCreate={false}
      />
    {:else}
      <NotesList
        notes={data.notes}
        currentNoteId={data.noteId}
        folderPath={data.folderPath}
        {slug}
        onCreateNote={handleCreateNote}
      />
    {/if}
  </section>

  <section class="explorer__preview">
    <a class="explorer__back" href={notesPath(slug, data.folderPath)}>‹ Wstecz</a>
    <NotePreview note={data.note} {slug} links={data.links} onmoved={handleMoveNote} />
  </section>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .explorer {
    display: grid;
    grid-template-columns: 200px 280px 1fr;
    height: calc(100dvh - 48px);
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
      display: flex;
      flex-direction: column;
    }

    &__tree {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }

    &__settings {
      flex-shrink: 0;
      border-top: 1px solid v.$border;
      padding: 10px 12px;
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      text-decoration: none;
      &:hover {
        color: v.$accent;
      }
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

  .tag-list-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid v.$border;
    flex-shrink: 0;
  }
  .tag-list-title {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$text-muted;
  }
  .desc-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: v.$font-mono;
    font-size: 0.68rem;
    color: v.$text-muted;
    cursor: pointer;
  }

  .explorer__back {
    display: none;
  }

  @include bp.mobile {
    .explorer {
      display: block;
      height: calc(100dvh - 48px);
      margin: 0;
      border: none;
      border-radius: 0;
      overflow-y: auto;
    }

    .explorer__sidebar {
      display: none;
    }

    .explorer__list,
    .explorer__preview {
      height: 100%;
    }

    // Mobile shows exactly one pane: list by default, preview when selected.
    .explorer__preview {
      display: none;
    }
    .explorer--preview .explorer__list {
      display: none;
    }
    .explorer--preview .explorer__preview {
      display: flex;
    }

    .explorer__back {
      display: block;
      flex-shrink: 0;
      padding: 10px 12px;
      border-bottom: 1px solid v.$border;
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$accent-dark;
      text-decoration: none;
    }
  }
</style>
