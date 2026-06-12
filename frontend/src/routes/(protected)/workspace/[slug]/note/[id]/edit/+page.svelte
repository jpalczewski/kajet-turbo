<script lang="ts">
  import { goto, invalidate } from '$app/navigation';
  import {
    apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete,
    apiUpdateNoteApiWorkspacesNameNotesNoteIdPatch,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import Breadcrumb from '$lib/components/Breadcrumb.svelte';
  import { notePath, notesPath } from '$lib/routes';

  let { data } = $props();
  let note = $derived(data.note);
  let slug = $derived(data.slug);

  let title = $state('');
  let content = $state('');

  $effect(() => {
    title = data.note.title;
    content = data.note.content ?? '';
  });
  let saveError = $state('');
  let saving = $state(false);

  async function handleSave() {
    saving = true;
    saveError = '';
    try {
      await apiUpdateNoteApiWorkspacesNameNotesNoteIdPatch(
        slug,
        note.note_id,
        jsonBody({ title: title.trim(), content }),
      );
      await invalidate('app:workspace-tree');
      goto(notePath(slug, note.note_id));
    } catch (e) {
      saveError = apiErrorMessage(e, 'Nie udało się zapisać');
    } finally {
      saving = false;
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Usunąć notatkę "${note.title}"?`)) return;
    try {
      await apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete(slug, note.note_id);
    } catch (e) {
      saveError = apiErrorMessage(e, 'Nie udało się usunąć');
      return;
    }
    await invalidate('app:workspace-tree');
    goto(notesPath(slug, note.folder ?? ''));
  }

  function handleCancel() {
    goto(notePath(slug, note.note_id));
  }
</script>

<main class="page">
  <Breadcrumb {slug} folder={note.folder} current="edycja" />

  <div class="form">
    <input class="form__title" bind:value={title} placeholder="Tytuł notatki" />
    <textarea class="form__content" bind:value={content} placeholder="Treść w Markdown..." rows={20}
    ></textarea>

    {#if saveError}
      <p class="form__error">{saveError}</p>
    {/if}

    <div class="form__actions">
      <button class="btn btn--primary" onclick={handleSave} disabled={saving}>
        {saving ? 'Zapisywanie…' : 'Zapisz'}
      </button>
      <button class="btn btn--secondary" onclick={handleCancel}>Anuluj</button>
      <button class="btn btn--danger" onclick={handleDelete}>Usuń</button>
    </div>
  </div>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .form {
    display: flex;
    flex-direction: column;
    gap: v.$space-md;

    &__title {
      font-family: v.$font-mono;
      font-size: 1.4rem;
      color: v.$text-primary;
      background: transparent;
      border: none;
      border-bottom: 1px solid v.$border;
      padding: v.$space-xs 0;
      outline: none;
      width: 100%;

      &:focus {
        border-bottom-color: v.$accent-dark;
      }
    }

    &__content {
      font-family: v.$font-mono;
      font-size: 0.9rem;
      color: v.$text-primary;
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      padding: v.$space-md;
      outline: none;
      resize: vertical;
      min-height: 300px;
      width: 100%;
      box-sizing: border-box;
      line-height: 1.6;

      &:focus {
        border-color: v.$accent-dark;
      }
    }

    &__error {
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: #c0392b;
      margin: 0;
    }

    &__actions {
      display: flex;
      gap: v.$space-sm;
      align-items: center;
    }
  }
</style>
