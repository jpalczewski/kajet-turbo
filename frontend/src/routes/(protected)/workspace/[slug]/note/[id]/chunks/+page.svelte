<script lang="ts">
  import ChunkList from '$lib/components/note/ChunkList.svelte';
  import { notePath } from '$lib/routes';

  let { data } = $props();
</script>

<main class="page">
  <a href={notePath(data.slug, data.noteId)} class="back-link">← Wróć do notatki</a>
  <ChunkList preview={data.preview} showHeader />
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 1100px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .back-link {
    display: inline-block;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    text-decoration: none;
    margin-bottom: v.$space-lg;
    transition: color 0.15s;
    &:hover {
      color: v.$accent;
    }
  }
</style>
