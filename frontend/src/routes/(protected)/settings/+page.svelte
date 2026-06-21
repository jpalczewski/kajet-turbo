<script lang="ts">
  import { apiSetEmbeddingConfigApiMeEmbeddingConfigPut } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';

  let { data } = $props();

  // Snapshot the initial config into local form state. This page is not
  // invalidated, so capturing the initial value (not reacting to `data`) is
  // intentional — re-syncing would clobber in-progress edits.
  // svelte-ignore state_referenced_locally
  const initial = data.embedding;

  let selected = $state(initial.selected ?? initial.default_id ?? '');
  let apiKey = $state('');
  let hasKey = $state(initial.has_key);
  let message = $state('');
  let saving = $state(false);

  const hasBackends = initial.backends.length > 0;

  async function save(e: SubmitEvent) {
    e.preventDefault();
    saving = true;
    message = '';
    try {
      const res = await apiSetEmbeddingConfigApiMeEmbeddingConfigPut(
        jsonBody({ backend_id: selected || null, api_key: apiKey || undefined }),
      );
      hasKey = res.data.has_key;
      selected = res.data.backend_id ?? selected;
      apiKey = '';
      message = 'Zapisano.';
    } catch (e) {
      message = apiErrorMessage(e, 'Nie udało się zapisać.');
    } finally {
      saving = false;
    }
  }
</script>

<main class="page">
  <h1>Ustawienia instancji</h1>
  <p class="hint">Backend embeddingów używany do wyszukiwania semantycznego.</p>

  {#if !hasBackends}
    <p class="config-form__hint">Brak skonfigurowanych backendów na instancji.</p>
  {/if}

  <form onsubmit={save} class="config-form">
    {#if message}<p class="config-form__message">{message}</p>{/if}

    <label class="config-form__field">
      <span>Backend</span>
      <select bind:value={selected} disabled={!hasBackends}>
        {#each initial.backends as b (b.backend_id)}
          <option value={b.backend_id}>{b.backend_id} — {b.model} ({b.dim})</option>
        {/each}
      </select>
    </label>

    <label class="config-form__field">
      <span>Klucz API</span>
      <input
        type="password"
        bind:value={apiKey}
        placeholder={hasKey ? '•••••• (ustawiony — zostaw puste, aby zachować)' : 'Klucz API'}
        autocomplete="off"
        disabled={!hasBackends}
      />
    </label>

    <p class="config-form__status">Klucz: {hasKey ? 'ustawiony' : 'brak'}</p>

    <button type="submit" disabled={saving || !hasBackends} class="btn-primary config-form__btn">
      {saving ? '…' : 'Zapisz'}
    </button>
  </form>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .config-form {
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
    margin-top: v.$space-xl;
    max-width: 480px;

    &__hint {
      margin-top: v.$space-md;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }

    &__message {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }

    &__field {
      display: flex;
      flex-direction: column;
      gap: v.$space-xs;

      span {
        font-size: 0.8rem;
        font-family: v.$font-mono;
        color: v.$text-secondary;
      }

      select,
      input {
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

    &__status {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }

    &__btn {
      width: auto;
      align-self: flex-start;
      padding: 9px 18px;
    }
  }
</style>
