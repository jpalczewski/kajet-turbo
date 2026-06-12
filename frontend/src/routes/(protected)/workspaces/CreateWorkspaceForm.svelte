<script lang="ts">
  import { invalidate } from '$app/navigation';
  import { apiCreateWorkspaceApiWorkspacesPost } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';

  let name = $state('');
  let error = $state('');
  let creating = $state(false);

  async function create(e: SubmitEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    creating = true;
    error = '';
    try {
      await apiCreateWorkspaceApiWorkspacesPost(jsonBody({ name: trimmed }));
      name = '';
      await invalidate('app:workspaces');
    } catch (e) {
      error = apiErrorMessage(e, 'Błąd sieci. Spróbuj ponownie.');
    } finally {
      creating = false;
    }
  }
</script>

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

<style lang="scss">
  @use '$lib/styles/variables' as v;

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
        transition:
          border-color 0.15s,
          box-shadow 0.15s;

        &:focus {
          outline: none;
          border-color: v.$accent;
          box-shadow: 0 0 0 2px rgba(240, 184, 0, 0.12);
        }

        &::placeholder {
          color: v.$text-muted;
        }
        &:disabled {
          opacity: 0.5;
        }
      }
    }

    &__btn {
      width: auto;
      padding: 9px 18px;
      white-space: nowrap;
    }
  }
</style>
