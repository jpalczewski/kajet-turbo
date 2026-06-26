<script lang="ts">
  import {
    apiListEmbeddingProfilesApiMeEmbeddingProfilesGet,
    apiCreateEmbeddingProfileApiMeEmbeddingProfilesPost,
    apiActivateEmbeddingProfileApiMeEmbeddingProfilesProfileIdActivatePost,
    apiDeleteEmbeddingProfileApiMeEmbeddingProfilesProfileIdDelete,
    apiListSshKeysApiMeSshKeysGet,
    apiCreateSshKeyApiMeSshKeysPost,
    apiDeleteSshKeyApiMeSshKeysKeyIdDelete,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';

  let { data } = $props();

  // Seed local state from the load data. The list endpoint is the single source
  // of truth; we reassign `profiles` after every mutation via reload(), so we
  // intentionally snapshot the initial value rather than react to `data`.
  // svelte-ignore state_referenced_locally
  let profiles = $state(data.profiles);

  // Add-profile form state.
  let name = $state('');
  let baseUrl = $state('');
  let model = $state('');
  let apiKey = $state('');
  let error = $state('');
  let busy = $state(false);

  async function reload() {
    const r = await apiListEmbeddingProfilesApiMeEmbeddingProfilesGet();
    if (r.status === 200) profiles = r.data.profiles;
  }

  async function create(e: SubmitEvent) {
    e.preventDefault();
    const n = name.trim();
    const b = baseUrl.trim();
    const m = model.trim();
    if (!n || !b || !m) return;
    busy = true;
    error = '';
    try {
      await apiCreateEmbeddingProfileApiMeEmbeddingProfilesPost(
        jsonBody({ name: n, base_url: b, model: m, api_key: apiKey || undefined }),
      );
      name = '';
      baseUrl = '';
      model = '';
      apiKey = '';
      await reload();
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się zapisać profilu.');
    } finally {
      busy = false;
    }
  }

  async function activate(id: string) {
    busy = true;
    error = '';
    try {
      await apiActivateEmbeddingProfileApiMeEmbeddingProfilesProfileIdActivatePost(id);
      await reload();
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się aktywować profilu.');
    } finally {
      busy = false;
    }
  }

  async function remove(id: string) {
    busy = true;
    error = '';
    try {
      await apiDeleteEmbeddingProfileApiMeEmbeddingProfilesProfileIdDelete(id);
      await reload();
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się usunąć profilu.');
    } finally {
      busy = false;
    }
  }

  // SSH keys section.
  // svelte-ignore state_referenced_locally
  let keys = $state(data.keys);
  let keyName = $state('');
  let keyAlgorithm = $state('ed25519');
  let keyError = $state('');
  let keyBusy = $state(false);

  async function reloadKeys() {
    const r = await apiListSshKeysApiMeSshKeysGet();
    if (r.status === 200) keys = r.data.keys;
  }

  async function createKey(e: SubmitEvent) {
    e.preventDefault();
    const n = keyName.trim();
    if (!n) return;
    keyBusy = true;
    keyError = '';
    try {
      await apiCreateSshKeyApiMeSshKeysPost(jsonBody({ name: n, algorithm: keyAlgorithm }));
      keyName = '';
      await reloadKeys();
    } catch (err) {
      keyError = apiErrorMessage(err, 'Nie udało się wygenerować klucza.');
    } finally {
      keyBusy = false;
    }
  }

  async function deleteKey(id: string) {
    try {
      await apiDeleteSshKeyApiMeSshKeysKeyIdDelete(id);
      await reloadKeys();
    } catch (err) {
      keyError = apiErrorMessage(err, 'Nie udało się usunąć klucza.');
    }
  }

  async function copyPublicKey(publicKey: string) {
    await navigator.clipboard.writeText(publicKey);
  }
</script>

<main class="page">
  <h1>Profile embeddingów</h1>
  <p class="hint">Konfiguracja embedderów używanych do wyszukiwania semantycznego.</p>

  {#if error}<p class="profiles__error">{error}</p>{/if}

  {#if profiles.length === 0}
    <p class="profiles__empty">Brak profili — dodaj pierwszy poniżej.</p>
  {:else}
    <ul class="profiles">
      {#each profiles as p (p.id)}
        <li class="profile" class:profile--active={p.is_active}>
          <div class="profile__head">
            <span class="profile__name">{p.name}</span>
            {#if p.is_active}<span class="profile__badge">aktywny</span>{/if}
          </div>
          <dl class="profile__meta">
            <div>
              <dt>base_url</dt>
              <dd>{p.base_url}</dd>
            </div>
            <div>
              <dt>model</dt>
              <dd>{p.model}</dd>
            </div>
            <div>
              <dt>dim</dt>
              <dd>{p.dim}</dd>
            </div>
            <div>
              <dt>klucz</dt>
              <dd>{p.has_key ? 'tak' : 'nie'}</dd>
            </div>
          </dl>
          <div class="profile__actions">
            {#if !p.is_active}
              <button
                type="button"
                class="btn-primary profile__btn"
                disabled={busy}
                onclick={() => activate(p.id)}
              >
                Aktywuj
              </button>
            {/if}
            <button
              type="button"
              class="profile__btn profile__btn--danger"
              disabled={busy}
              onclick={() => remove(p.id)}
            >
              Usuń
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  <form onsubmit={create} class="add-form">
    <h2>Nowy profil</h2>

    <label class="add-form__field">
      <span>Nazwa</span>
      <input type="text" bind:value={name} autocomplete="off" spellcheck="false" disabled={busy} />
    </label>

    <label class="add-form__field">
      <span>base_url</span>
      <input
        type="text"
        bind:value={baseUrl}
        placeholder="https://api.example.com/v1"
        autocomplete="off"
        spellcheck="false"
        disabled={busy}
      />
    </label>

    <label class="add-form__field">
      <span>model</span>
      <input type="text" bind:value={model} autocomplete="off" spellcheck="false" disabled={busy} />
    </label>

    <label class="add-form__field">
      <span>Klucz API</span>
      <input
        type="password"
        bind:value={apiKey}
        placeholder="opcjonalny — dla endpointów bez klucza zostaw puste"
        autocomplete="off"
        disabled={busy}
      />
    </label>

    <button
      type="submit"
      class="btn-primary add-form__btn"
      disabled={busy || !name.trim() || !baseUrl.trim() || !model.trim()}
    >
      {busy ? '…' : 'Dodaj profil'}
    </button>
  </form>

  <section class="ssh-section">
    <h1>Klucze SSH</h1>
    <p class="hint">Klucze SSH używane do autopush workspace'ów do zdalnych repozytoriów.</p>

    {#if keyError}<p class="profiles__error">{keyError}</p>{/if}

    {#if keys.length === 0}
      <p class="profiles__empty">Brak kluczy SSH — wygeneruj pierwszy poniżej.</p>
    {:else}
      <ul class="profiles">
        {#each keys as key (key.id)}
          <li class="profile">
            <div class="profile__head">
              <span class="profile__name">{key.name}</span>
              <span class="profile__badge">{key.algorithm}</span>
            </div>
            <dl class="profile__meta">
              <div>
                <dt>fingerprint</dt>
                <dd>{key.fingerprint}</dd>
              </div>
            </dl>
            <div class="profile__actions">
              <button
                type="button"
                class="btn-primary profile__btn"
                onclick={() => copyPublicKey(key.public_key)}
              >
                Kopiuj klucz publiczny
              </button>
              <button
                type="button"
                class="profile__btn profile__btn--danger"
                onclick={() => deleteKey(key.id)}
              >
                Usuń
              </button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}

    <form onsubmit={createKey} class="add-form">
      <h2>Nowy klucz</h2>

      <label class="add-form__field">
        <span>Nazwa klucza</span>
        <input
          type="text"
          bind:value={keyName}
          placeholder="Nazwa klucza"
          autocomplete="off"
          spellcheck="false"
          disabled={keyBusy}
        />
      </label>

      <label class="add-form__field">
        <span>Algorytm</span>
        <select bind:value={keyAlgorithm} disabled={keyBusy} class="add-form__select">
          <option value="ed25519">ed25519</option>
          <option value="ecdsa-p256">ecdsa-p256</option>
          <option value="rsa-4096">rsa-4096</option>
        </select>
      </label>

      <button type="submit" class="btn-primary add-form__btn" disabled={keyBusy || !keyName.trim()}>
        {keyBusy ? '…' : 'Generuj'}
      </button>
    </form>
  </section>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .profiles {
    list-style: none;
    padding: 0;
    margin: v.$space-xl 0;
    display: flex;
    flex-direction: column;
    gap: v.$space-md;

    &__error {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$error;
      margin-top: v.$space-md;
    }

    &__empty {
      margin-top: v.$space-xl;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }
  }

  .profile {
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    padding: v.$space-md v.$space-lg;
    background: v.$bg-surface;

    &--active {
      border-color: v.$accent;
    }

    &__head {
      display: flex;
      align-items: center;
      gap: v.$space-sm;
      margin-bottom: v.$space-sm;
    }

    &__name {
      font-weight: 600;
      color: v.$text-primary;
    }

    &__badge {
      font-size: 0.7rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: v.$accent;
      border: 1px solid v.$accent;
      border-radius: v.$radius-sm;
      padding: 1px 6px;
    }

    &__meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: v.$space-xs v.$space-md;
      margin: 0 0 v.$space-md;

      div {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }

      dt {
        font-size: 0.7rem;
        font-family: v.$font-mono;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: v.$text-muted;
      }

      dd {
        margin: 0;
        font-size: 0.85rem;
        font-family: v.$font-mono;
        color: v.$text-secondary;
        word-break: break-all;
      }
    }

    &__actions {
      display: flex;
      gap: v.$space-sm;
    }

    &__btn {
      width: auto;
      padding: 6px 14px;
      font-size: 0.85rem;

      &--danger {
        background: transparent;
        border: 1px solid v.$border;
        color: v.$text-secondary;

        &:hover:not(:disabled) {
          border-color: v.$error;
          color: v.$error;
        }
      }

      &:disabled {
        opacity: 0.5;
      }
    }
  }

  .add-form {
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
    margin-top: v.$space-2xl;
    max-width: 480px;
    border-top: 1px solid v.$border;
    padding-top: v.$space-xl;

    h2 {
      margin: 0;
      font-size: 1.1rem;
      color: v.$text-primary;
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

    &__btn {
      width: auto;
      align-self: flex-start;
      padding: 9px 18px;
    }

    &__select {
      padding: 9px 12px;
      background: v.$bg-surface;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      color: v.$text-primary;
      font-size: 0.9rem;
      font-family: v.$font-mono;

      &:focus {
        outline: none;
        border-color: v.$accent;
        box-shadow: 0 0 0 2px rgba(240, 184, 0, 0.12);
      }

      &:disabled {
        opacity: 0.5;
      }
    }
  }

  .ssh-section {
    margin-top: v.$space-2xl;
    padding-top: v.$space-xl;
    border-top: 1px solid v.$border;
  }
</style>
