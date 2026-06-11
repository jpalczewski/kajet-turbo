<script lang="ts">
  import { goto } from '$app/navigation'

  let { data } = $props()
  let note = $derived(data.note)
  let slug = $derived(data.slug)

  let title = $state(note.title as string)
  let content = $state((note.content as string) ?? '')
  let saveError = $state('')
  let saving = $state(false)

  async function handleSave() {
    saving = true
    saveError = ''
    try {
      const resp = await fetch(`/api/workspaces/${slug}/notes/${note.note_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ title: title.trim(), content }),
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        saveError = body.error ?? 'Nie udało się zapisać'
        return
      }
      goto(`/workspace/${slug}/note/${note.note_id}`)
    } finally {
      saving = false
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Usunąć notatkę "${note.title}"?`)) return
    const resp = await fetch(`/api/workspaces/${slug}/notes/${note.note_id}`, {
      method: 'DELETE',
      credentials: 'include',
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      saveError = body.error ?? 'Nie udało się usunąć'
      return
    }
    const folderPath = note.folder ?? ''
    goto(folderPath ? `/workspace/${slug}/notes/${folderPath}` : `/workspace/${slug}/notes`)
  }

  function handleCancel() {
    goto(`/workspace/${slug}/note/${note.note_id}`)
  }
</script>

<main class="page">
  <nav class="breadcrumb">
    <a href="/workspaces" class="breadcrumb__link">Workspaces</a>
    <span class="breadcrumb__sep">/</span>
    <a href="/workspace/{slug}/notes" class="breadcrumb__link">{slug}</a>
    {#if note.folder}
      {#each note.folder.split('/') as segment}
        <span class="breadcrumb__sep">/</span>
        <span class="breadcrumb__folder">{segment}</span>
      {/each}
    {/if}
    <span class="breadcrumb__sep">/</span>
    <span class="breadcrumb__current">edycja</span>
  </nav>

  <div class="form">
    <input
      class="form__title"
      bind:value={title}
      placeholder="Tytuł notatki"
    />
    <textarea
      class="form__content"
      bind:value={content}
      placeholder="Treść w Markdown..."
      rows={20}
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

  .breadcrumb {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    margin-bottom: v.$space-xl;
    font-size: 0.75rem;
    font-family: v.$font-mono;

    &__link {
      color: v.$accent-dark;
      text-decoration: none;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      transition: color 0.15s;

      &:hover { color: v.$accent; }
    }

    &__sep { color: v.$text-muted; }

    &__folder {
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    &__current {
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
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

      &:focus { border-bottom-color: v.$accent-dark; }
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

      &:focus { border-color: v.$accent-dark; }
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

  .btn {
    font-family: v.$font-mono;
    font-size: 0.8rem;
    padding: v.$space-sm v.$space-lg;
    border-radius: v.$radius-sm;
    border: 1px solid v.$border;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
    letter-spacing: 0.04em;
    text-transform: uppercase;

    &--primary {
      background: v.$accent-dark;
      color: v.$bg-deep;
      border-color: v.$accent-dark;

      &:hover:not(:disabled) { background: v.$accent; border-color: v.$accent; }
      &:disabled { opacity: 0.5; cursor: not-allowed; }
    }

    &--secondary {
      background: none;
      color: v.$text-secondary;

      &:hover { color: v.$text-primary; background: rgba(255,255,255,0.04); }
    }

    &--danger {
      background: none;
      color: #c0392b;
      border-color: transparent;
      margin-left: auto;

      &:hover { background: rgba(192, 57, 43, 0.1); }
    }
  }
</style>
