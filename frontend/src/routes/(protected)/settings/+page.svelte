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
  import { jsonBody } from '$lib/api/mutate';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';

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

  const profileAction = useAsyncAction();

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
    await profileAction.run(async () => {
      await apiCreateEmbeddingProfileApiMeEmbeddingProfilesPost(
        jsonBody({ name: n, base_url: b, model: m, api_key: apiKey || undefined }),
      );
      name = '';
      baseUrl = '';
      model = '';
      apiKey = '';
      await reload();
    }, 'Nie udało się zapisać profilu.');
  }

  async function activate(id: string) {
    await profileAction.run(async () => {
      await apiActivateEmbeddingProfileApiMeEmbeddingProfilesProfileIdActivatePost(id);
      await reload();
    }, 'Nie udało się aktywować profilu.');
  }

  async function remove(id: string) {
    await profileAction.run(async () => {
      await apiDeleteEmbeddingProfileApiMeEmbeddingProfilesProfileIdDelete(id);
      await reload();
    }, 'Nie udało się usunąć profilu.');
  }

  // SSH keys section.
  // svelte-ignore state_referenced_locally
  let keys = $state(data.keys);
  let keyName = $state('');
  let keyAlgorithm = $state('ed25519');

  const keyAction = useAsyncAction();

  async function reloadKeys() {
    const r = await apiListSshKeysApiMeSshKeysGet();
    if (r.status === 200) keys = r.data.keys;
  }

  async function createKey(e: SubmitEvent) {
    e.preventDefault();
    const n = keyName.trim();
    if (!n) return;
    await keyAction.run(async () => {
      await apiCreateSshKeyApiMeSshKeysPost(jsonBody({ name: n, algorithm: keyAlgorithm }));
      keyName = '';
      await reloadKeys();
    }, 'Nie udało się wygenerować klucza.');
  }

  async function deleteKey(id: string) {
    await keyAction.run(async () => {
      await apiDeleteSshKeyApiMeSshKeysKeyIdDelete(id);
      await reloadKeys();
    }, 'Nie udało się usunąć klucza.');
  }

  async function copyPublicKey(publicKey: string) {
    await navigator.clipboard.writeText(publicKey);
  }
</script>

<main class="page">
  <h1>Profile embeddingów</h1>
  <p class="hint">Konfiguracja embedderów używanych do wyszukiwania semantycznego.</p>

  {#if profileAction.error}<p class="profiles__error">{profileAction.error}</p>{/if}

  {#if profiles.length === 0}
    <EmptyState>Brak profili — dodaj pierwszy poniżej.</EmptyState>
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
                disabled={profileAction.busy}
                onclick={() => activate(p.id)}
              >
                Aktywuj
              </button>
            {/if}
            <button
              type="button"
              class="btn-danger"
              disabled={profileAction.busy}
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
      <input
        type="text"
        bind:value={name}
        autocomplete="off"
        spellcheck="false"
        disabled={profileAction.busy}
      />
    </label>

    <label class="add-form__field">
      <span>base_url</span>
      <input
        type="text"
        bind:value={baseUrl}
        placeholder="https://api.example.com/v1"
        autocomplete="off"
        spellcheck="false"
        disabled={profileAction.busy}
      />
    </label>

    <label class="add-form__field">
      <span>model</span>
      <input
        type="text"
        bind:value={model}
        autocomplete="off"
        spellcheck="false"
        disabled={profileAction.busy}
      />
    </label>

    <label class="add-form__field">
      <span>Klucz API</span>
      <input
        type="password"
        bind:value={apiKey}
        placeholder="opcjonalny — dla endpointów bez klucza zostaw puste"
        autocomplete="off"
        disabled={profileAction.busy}
      />
    </label>

    <button
      type="submit"
      class="btn-primary add-form__btn"
      disabled={profileAction.busy || !name.trim() || !baseUrl.trim() || !model.trim()}
    >
      {profileAction.busy ? '…' : 'Dodaj profil'}
    </button>
  </form>

  <section class="ssh-section">
    <h1>Klucze SSH</h1>
    <p class="hint">Klucze SSH używane do autopush workspace'ów do zdalnych repozytoriów.</p>

    {#if keyAction.error}<p class="profiles__error">{keyAction.error}</p>{/if}

    {#if keys.length === 0}
      <EmptyState>Brak kluczy SSH — wygeneruj pierwszy poniżej.</EmptyState>
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
              <button type="button" class="btn-danger" onclick={() => deleteKey(key.id)}>
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
          disabled={keyAction.busy}
        />
      </label>

      <label class="add-form__field">
        <span>Algorytm</span>
        <select bind:value={keyAlgorithm} disabled={keyAction.busy} class="add-form__select">
          <option value="ed25519">ed25519</option>
          <option value="ecdsa-p256">ecdsa-p256</option>
          <option value="rsa-4096">rsa-4096</option>
        </select>
      </label>

      <button
        type="submit"
        class="btn-primary add-form__btn"
        disabled={keyAction.busy || !keyName.trim()}
      >
        {keyAction.busy ? '…' : 'Generuj'}
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
