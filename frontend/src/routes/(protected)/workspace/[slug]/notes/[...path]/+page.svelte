<script lang="ts">
  import FolderTree from './FolderTree.svelte'
  import NotesList from './NotesList.svelte'
  import NotePreview from './NotePreview.svelte'

  let { data } = $props()
  let slug = data.slug
</script>

<div class="explorer">
  <aside class="explorer__sidebar">
    <FolderTree
      folders={data.tree.folders}
      currentFolder={data.folderPath}
      {slug}
    />
  </aside>

  <section class="explorer__list">
    <NotesList
      notes={data.notes}
      currentNoteId={data.noteId}
      folderPath={data.folderPath}
      {slug}
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
