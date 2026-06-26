<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import {
    apiReindexWorkspaceApiWorkspacesNameReindexPost,
    apiGetWorkspaceSettingsApiWorkspacesNameSettingsGet as getSettings,
    apiUpdateWorkspaceSettingsApiWorkspacesNameSettingsPatch as patchSettings,
    type SettingDefinition,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';

  const slug = $derived(page.params.slug as string);

  let reindexing = $state(false);
  let reindexMsg = $state('');

  async function reindex() {
    reindexing = true;
    reindexMsg = '';
    try {
      const res = await apiReindexWorkspaceApiWorkspacesNameReindexPost(slug);
      reindexMsg =
        res.status === 200
          ? `Zreindeksowano ${res.data.count} notatek.`
          : 'Nie udało się zreindeksować.';
    } catch (e) {
      reindexMsg = apiErrorMessage(e, 'Nie udało się zreindeksować.');
    } finally {
      reindexing = false;
    }
  }

  let definitions = $state<SettingDefinition[]>([]);
  let values = $state<Record<string, unknown>>({});
  let settingsError = $state('');

  onMount(async () => {
    try {
      const res = await getSettings(slug);
      if (res.status === 200) {
        definitions = res.data.definitions;
        values = res.data.values;
      }
    } catch (e) {
      settingsError = apiErrorMessage(e, 'Nie udało się wczytać ustawień.');
    }
  });

  async function toggle(key: string) {
    const prev = values[key];
    values[key] = !prev;
    settingsError = '';
    try {
      const res = await patchSettings(slug, jsonBody({ values: { [key]: values[key] } }));
      if (res.status === 200) {
        values = res.data.values;
      } else {
        throw new Error();
      }
    } catch (e) {
      values[key] = prev;
      settingsError = apiErrorMessage(e, 'Nie udało się zapisać ustawienia.');
    }
  }
</script>

<main class="page">
  <h1>Ustawienia — {page.params.slug}</h1>

  <section class="settings">
    <h2>Ustawienia workspace'u</h2>
    {#each definitions as def (def.key)}
      {#if def.type === 'bool'}
        <label class="settings__row">
          <input type="checkbox" checked={!!values[def.key]} onchange={() => toggle(def.key)} />
          <span class="settings__label">{def.label}</span>
          <span class="settings__hint">{def.description}</span>
        </label>
      {/if}
    {/each}
    {#if settingsError}<p class="settings__error">{settingsError}</p>{/if}
  </section>

  <section class="reindex">
    <h2>Indeks wyszukiwania</h2>
    <p class="hint">Przebudowuje indeks wyszukiwania (chunki + wektory) z plików notatek.</p>
    <button type="button" class="btn-primary reindex__btn" disabled={reindexing} onclick={reindex}>
      {reindexing ? 'Reindeksowanie…' : 'Reindeksuj workspace'}
    </button>
    {#if reindexMsg}<p class="reindex__msg">{reindexMsg}</p>{/if}
  </section>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .settings {
    margin-top: v.$space-lg;

    h2 {
      font-size: 1.1rem;
      margin-bottom: v.$space-sm;
    }

    &__row {
      display: flex;
      align-items: baseline;
      gap: v.$space-sm;
      padding: v.$space-sm 0;
      cursor: pointer;

      input[type='checkbox'] {
        flex-shrink: 0;
        margin-top: 2px;
      }
    }

    &__label {
      font-weight: 500;
    }

    &__hint {
      font-size: 0.85rem;
      color: v.$text-secondary;
    }

    &__error {
      margin-top: v.$space-sm;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }
  }

  .reindex {
    margin-top: v.$space-lg;

    h2 {
      font-size: 1.1rem;
      margin-bottom: v.$space-sm;
    }

    &__btn {
      width: auto;
      padding: 9px 18px;
      white-space: nowrap;
    }

    &__msg {
      margin-top: v.$space-sm;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }
  }
</style>
