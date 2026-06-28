<script lang="ts">
  import {
    apiGetFolderMetaApiWorkspacesNameFoldersPathMetaGet,
    apiUpdateFolderMetaApiWorkspacesNameFoldersPathMetaPut,
  } from '$lib/api';
  import Modal from '$lib/components/ui/Modal.svelte';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';

  let {
    slug,
    folder,
    onupdated,
  }: {
    slug: string;
    folder: string;
    onupdated?: () => void | Promise<void>;
  } = $props();

  let modal: Modal;
  let description = $state('');
  let instructions = $state('');
  const fetchAction = useAsyncAction();
  const saveAction = useAsyncAction();

  export async function open() {
    saveAction.clearError();
    modal.show();
    await fetchAction.run(async () => {
      const result = await apiGetFolderMetaApiWorkspacesNameFoldersPathMetaGet(
        slug,
        folder || 'root',
      );
      if (result.status !== 200) throw new Error();
      description = result.data.description;
      instructions = result.data.instructions;
    }, 'Nie udało się załadować metadanych folderu');
  }

  async function save() {
    if (fetchAction.busy || saveAction.busy) return;
    await saveAction.run(async () => {
      const result = await apiUpdateFolderMetaApiWorkspacesNameFoldersPathMetaPut(
        slug,
        folder || 'root',
        { description, instructions },
      );
      if (result.status !== 200) throw new Error();
      await onupdated?.();
      modal.close();
    }, 'Nie udało się zapisać');
  }
</script>

<Modal bind:this={modal} title="Metadane folderu — {folder || slug}">
  {#if fetchAction.busy}
    <p class="status">Ładowanie…</p>
  {:else}
    <label class="field">
      Opis
      <textarea
        rows="2"
        placeholder="Do czego służy ten folder"
        bind:value={description}
        disabled={saveAction.busy}
      ></textarea>
    </label>
    <label class="field">
      Instrukcje dla LLM
      <textarea
        rows="4"
        placeholder="Wskazówki dla asystenta przy pracy z notatkami w tym folderze"
        bind:value={instructions}
        disabled={saveAction.busy}
      ></textarea>
    </label>
  {/if}

  {#if fetchAction.error || saveAction.error}
    <p class="error">{fetchAction.error || saveAction.error}</p>
  {/if}

  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={saveAction.busy}>
      Anuluj
    </button>
    <button class="btn btn--primary" onclick={save} disabled={fetchAction.busy || saveAction.busy}>
      {saveAction.busy ? 'Zapisywanie…' : 'Zapisz'}
    </button>
  {/snippet}
</Modal>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .field {
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    color: v.$text-muted;

    textarea {
      width: 100%;
      padding: 9px 12px;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      background: v.$bg-surface;
      color: v.$text-primary;
      font-family: v.$font-mono;
      font-size: 0.82rem;
      resize: vertical;
      box-sizing: border-box;

      &:focus {
        outline: none;
        border-color: v.$accent;
      }
    }
  }

  .status {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    color: v.$text-muted;
  }

  .error {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    color: v.$error;
  }
</style>
