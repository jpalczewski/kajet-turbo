<script lang="ts">
  import { invalidate, goto } from '$app/navigation'
  import FolderTree from './FolderTree.svelte'
  import NotesList from './NotesList.svelte'
  import NotePreview from './NotePreview.svelte'

  let { data } = $props()
  let slug = $derived(data.slug)

  async function handleCreateFolder(path: string): Promise<void> {
    const resp = await fetch(`/api/workspaces/${slug}/folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ path }),
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw new Error(body.error ?? 'Nie udało się utworzyć folderu')
    }
    await invalidate('app:workspace-tree')
    goto(`/workspace/${slug}/notes/${path}`)
  }

  async function handleCreateNote(title: string): Promise<void> {
    const resp = await fetch(`/api/workspaces/${slug}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ title, folder: data.folderPath, content: '' }),
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw new Error(body.error ?? 'Nie udało się utworzyć notatki')
    }
    const { note_id } = await resp.json()
    await invalidate('app:workspace-tree')
    goto(`/workspace/${slug}/note/${note_id}/edit`)
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
