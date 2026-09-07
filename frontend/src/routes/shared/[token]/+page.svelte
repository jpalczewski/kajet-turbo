<script lang="ts">
  import { page } from '$app/state';
  import NoteBody from '$lib/components/note/NoteBody.svelte';

  const note = $derived(page.data.note);
  const transientError = $derived(page.data.transientError);
</script>

<svelte:head>
  <title>{note ? note.title : 'Link nieaktywny'} — kajet</title>
</svelte:head>

<main class="shared">
  {#if note}
    <h1 class="shared__title">{note.title}</h1>
    <NoteBody slug="" noteId={note.note_id} html={note.content_html} mode="content" />
  {:else if transientError}
    <p class="shared__missing">Nie udało się wczytać notatki. Spróbuj ponownie za chwilę.</p>
  {:else}
    <p class="shared__missing">Ten link jest nieaktualny albo nigdy nie istniał.</p>
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .shared {
    max-width: 700px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .shared__title {
    font-size: 1.75rem;
    font-family: v.$font-mono;
    color: v.$text-primary;
    margin: 0 0 v.$space-xl 0;
    line-height: 1.3;
  }

  .shared__missing {
    font-family: v.$font-mono;
    color: v.$text-muted;
  }
</style>
