<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import {
    apiDeleteWorkspaceApiWorkspacesNameDelete,
    apiReindexWorkspaceApiWorkspacesNameReindexPost,
    apiGetWorkspaceSettingsApiWorkspacesNameSettingsGet as getSettings,
    apiUpdateWorkspaceSettingsApiWorkspacesNameSettingsPatch as patchSettings,
    type SettingDefinition,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import { workspaceExportUrl, workspacesPath } from '$lib/routes';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';

  const slug = $derived(page.params.slug as string);

  async function deleteWorkspace() {
    try {
      await apiDeleteWorkspaceApiWorkspacesNameDelete(slug);
    } catch (e) {
      throw new Error(apiErrorMessage(e, 'Nie udało się usunąć workspace.'), { cause: e });
    }
    await goto(workspacesPath(), { invalidateAll: true });
  }

  const reindexAction = useAsyncAction();
  let reindexMsg = $state('');

  async function reindex() {
    reindexMsg = '';
    await reindexAction.run(async () => {
      const res = await apiReindexWorkspaceApiWorkspacesNameReindexPost(slug);
      reindexMsg = res.status === 200 ? `Zreindeksowano ${res.data.count} notatek.` : '';
      if (res.status !== 200) throw new Error('Nie udało się zreindeksować.');
    }, 'Nie udało się zreindeksować.');
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
    <button
      type="button"
      class="btn-primary reindex__btn"
      disabled={reindexAction.busy}
      onclick={reindex}
    >
      {reindexAction.busy ? 'Reindeksowanie…' : 'Reindeksuj workspace'}
    </button>
    {#if reindexAction.error}<p class="reindex__error">{reindexAction.error}</p>{/if}
    {#if reindexMsg}<p class="reindex__msg">{reindexMsg}</p>{/if}
  </section>

  <section class="export">
    <h2>Eksport</h2>
    <p class="hint">
      Pobierz spójny snapshot plików z aktualnego commita albo pełną historię workspace'u.
    </p>
    <div class="export__actions">
      <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- API download, not SPA navigation -->
      <a class="btn-ghost export__btn" href={workspaceExportUrl(slug, 'zip')}> Snapshot (.zip) </a>
      <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- API download, not SPA navigation -->
      <a class="btn-ghost export__btn" href={workspaceExportUrl(slug, 'tar.zst')}>
        Snapshot (.tar.zst)
      </a>
      <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- API download, not SPA navigation -->
      <a class="btn-ghost export__btn" href={workspaceExportUrl(slug, 'bundle')}>
        Pełna historia Git (.bundle)
      </a>
    </div>
    <p class="export__hint">
      Bundle odtworzysz poleceniem <code>git clone nazwa.bundle katalog</code>. Format
      <code>.tar.zst</code> wymaga obsługi Zstandard.
    </p>
  </section>

  <section class="danger">
    <h2>Strefa niebezpieczna</h2>
    <p class="hint">
      Usuwa workspace <strong>{slug}</strong> bezpowrotnie: wszystkie notatki, historię git i ustawienia.
      Tej operacji nie da się cofnąć.
    </p>
    <ConfirmDialog
      title="Usuń workspace"
      message={`Usunąć workspace "${slug}" wraz z całą zawartością i historią? Tej operacji nie da się cofnąć.`}
      confirmLabel="Usuń workspace"
      confirmVariant="danger"
      confirmText={slug}
      onconfirm={deleteWorkspace}
    >
      {#snippet trigger({ open })}
        <button type="button" class="btn-danger danger__btn" onclick={open}>
          Usuń workspace
        </button>
      {/snippet}
    </ConfirmDialog>
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

    &__error {
      margin-top: v.$space-sm;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }

    &__msg {
      margin-top: v.$space-sm;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }
  }

  .danger {
    margin-top: v.$space-lg;
    padding-top: v.$space-lg;
    border-top: 1px solid v.$border;

    h2 {
      font-size: 1.1rem;
      margin-bottom: v.$space-sm;
      color: v.$error;
    }

    &__btn {
      width: auto;
      padding: 9px 18px;
      white-space: nowrap;
      margin-top: v.$space-sm;
    }
  }

  .export {
    margin-top: v.$space-lg;
    padding-top: v.$space-lg;
    border-top: 1px solid v.$border;

    h2 {
      font-size: 1.1rem;
      margin-bottom: v.$space-sm;
    }

    &__actions {
      display: flex;
      flex-wrap: wrap;
      gap: v.$space-sm;
      margin-top: v.$space-md;
    }

    &__btn {
      width: auto;
      text-decoration: none;
    }

    &__hint {
      margin-top: v.$space-md;
      font-size: 0.8rem;
      color: v.$text-secondary;

      code {
        font-family: v.$font-mono;
        color: v.$text-primary;
      }
    }
  }
</style>
