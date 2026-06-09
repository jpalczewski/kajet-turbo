<script lang="ts">
  import { page } from '$app/state'
  import { invalidateAll } from '$app/navigation'
  import { apiCreateWorkspaceApiWorkspacesPost } from '$lib/api'

  let workspaces = $derived(page.data.workspaces ?? [])
  let name = $state('')
  let error = $state('')
  let creating = $state(false)

  async function create(e: SubmitEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    creating = true
    error = ''
    try {
      const result = await apiCreateWorkspaceApiWorkspacesPost({
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      })
      if (result.status === 200) {
        name = ''
        await invalidateAll()
      } else {
        error = (result.data as any)?.error ?? 'Błąd tworzenia workspace.'
      }
    } catch {
      error = 'Błąd sieci. Spróbuj ponownie.'
    } finally {
      creating = false
    }
  }
</script>

<main class="page">
  <header class="page__header">
    <h1>Workspaces</h1>
    <span class="page__count">{workspaces.length}</span>
  </header>

  <form onsubmit={create} class="create-form">
    {#if error}<p class="create-form__error">{error}</p>{/if}
    <div class="create-form__row">
      <input
        type="text"
        bind:value={name}
        placeholder="nazwa-workspace"
        autocomplete="off"
        spellcheck="false"
        disabled={creating}
      />
      <button type="submit" disabled={creating || !name.trim()} class="btn-primary create-form__btn">
        {creating ? '…' : '+ Nowy'}
      </button>
    </div>
  </form>

  {#if workspaces.length === 0}
    <p class="empty">Brak workspace'ów. Utwórz pierwszy powyżej.</p>
  {:else}
    <ul class="ws-list">
      {#each workspaces as ws}
        <li class="ws-card">
          <a href="/workspace/{ws}/notes" class="ws-card__link">
            <span class="ws-card__name">{ws}</span>
            <span class="ws-card__arrow">→</span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 640px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .page__header {
    display: flex;
    align-items: baseline;
    gap: v.$space-md;
    margin-bottom: v.$space-xl;

    h1 {
      font-size: 1.5rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-primary;
      margin: 0;
    }
  }

  .page__count {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$accent-dark;
    background: rgba(240, 184, 0, 0.08);
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    padding: 2px 8px;
  }

  .create-form {
    margin-bottom: v.$space-xl;

    &__error {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$error;
      margin-bottom: v.$space-sm;
    }

    &__row {
      display: flex;
      gap: v.$space-sm;

      input {
        flex: 1;
        padding: 9px 12px;
        background: v.$bg-surface;
        border: 1px solid v.$border;
        border-radius: v.$radius-md;
        color: v.$text-primary;
        font-size: 0.9rem;
        font-family: v.$font-mono;
        transition: border-color 0.15s, box-shadow 0.15s;

        &:focus {
          outline: none;
          border-color: v.$accent;
          box-shadow: 0 0 0 2px rgba(240, 184, 0, 0.12);
        }

        &::placeholder { color: v.$text-muted; }
        &:disabled { opacity: 0.5; }
      }
    }

    &__btn {
      width: auto;
      padding: 9px 18px;
      white-space: nowrap;
    }
  }

  .empty {
    font-size: 0.85rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .ws-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;
  }

  .ws-card {
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    transition: border-color 0.15s;

    &:hover { border-color: v.$accent-dark; }

    &__link {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: v.$space-md v.$space-lg;
      text-decoration: none;
      color: v.$text-primary;
    }

    &__name {
      font-size: 0.9rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    &__arrow {
      color: v.$accent-dark;
      font-size: 0.85rem;
      transition: color 0.15s, transform 0.15s;
    }

    &:hover &__arrow {
      color: v.$accent;
      transform: translateX(3px);
    }
  }
</style>
