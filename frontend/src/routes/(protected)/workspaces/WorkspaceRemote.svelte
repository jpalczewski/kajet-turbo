<script lang="ts">
  import {
    apiGetWorkspaceRemoteApiWorkspacesNameRemoteGet,
    apiSetWorkspaceRemoteApiWorkspacesNameRemotePut,
    apiDeleteWorkspaceRemoteApiWorkspacesNameRemoteDelete,
    apiTriggerWorkspacePushApiWorkspacesNameRemotePushPost,
    type SshKeyItem,
    type WorkspaceRemoteView,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import { settingsPath } from '$lib/routes';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';

  let { name, keys }: { name: string; keys: SshKeyItem[] } = $props();

  let remote = $state<WorkspaceRemoteView | null>(null);
  let originUrl = $state('');
  let sshKeyId = $state('');
  let enabled = $state(true);
  let loaded = $state(false);

  const action = useAsyncAction();

  async function load() {
    try {
      const r = await apiGetWorkspaceRemoteApiWorkspacesNameRemoteGet(name);
      if (r.status === 200) {
        remote = r.data.remote;
        if (remote) {
          originUrl = remote.origin_url;
          sshKeyId = remote.ssh_key_id;
          enabled = remote.enabled;
        }
      }
    } catch {
      // Remote not configured or network error — leave remote as null
    }
    loaded = true;
  }

  // Mirror of the backend rule: only SSH origins (scp-like or ssh://); reject HTTP(S).
  const SSH_REMOTE = /^(ssh:\/\/\S+|[\w.+-]+@[\w.-]+:\S+)$/i;

  async function save(e: SubmitEvent) {
    e.preventDefault();
    if (!originUrl.trim() || !sshKeyId) return;

    await action.run(async () => {
      if (!SSH_REMOTE.test(originUrl.trim())) {
        throw new Error('Origin musi być adresem SSH (git@host:repo.git lub ssh://…), nie HTTPS.');
      }
      const r = await apiSetWorkspaceRemoteApiWorkspacesNameRemotePut(
        name,
        jsonBody({ origin_url: originUrl.trim(), ssh_key_id: sshKeyId, enabled }),
      );
      if (r.status === 200) {
        remote = (r.data as { remote: WorkspaceRemoteView }).remote;
      }
    }, 'Nie udało się zapisać remote.');
  }

  async function remove() {
    await action.run(async () => {
      const r = await apiDeleteWorkspaceRemoteApiWorkspacesNameRemoteDelete(name);
      if (r.status !== 200) {
        throw new Error(apiErrorMessage(r, 'Nie udało się usunąć remote.'));
      }
      remote = null;
      originUrl = '';
      sshKeyId = '';
      enabled = true;
    }, 'Nie udało się usunąć remote.');
  }

  async function pushNow() {
    await action.run(async () => {
      const r = await apiTriggerWorkspacePushApiWorkspacesNameRemotePushPost(name);
      if (r.status === 200) {
        await load();
      } else {
        throw new Error(apiErrorMessage(r, 'Push nie powiódł się.'));
      }
    }, 'Push nie powiódł się.');
  }

  $effect(() => {
    if (!loaded) load();
  });
</script>

<div class="remote">
  <h3 class="remote__heading">Remote (auto-push)</h3>

  {#if !loaded}
    <p class="remote__hint">Ładowanie…</p>
  {:else if keys.length === 0}
    <p class="remote__hint">
      Brak kluczy SSH. Utwórz klucz w <a href={settingsPath()}>ustawieniach</a>, aby skonfigurować
      remote.
    </p>
  {:else}
    <form class="remote__form" onsubmit={save}>
      {#if action.error}
        <p class="remote__error">{action.error}</p>
      {/if}

      <label class="remote__label">
        Origin URL
        <input
          type="text"
          bind:value={originUrl}
          placeholder="git@github.com:user/repo.git"
          disabled={action.busy}
          spellcheck="false"
          autocomplete="off"
        />
      </label>

      <label class="remote__label">
        Klucz SSH
        <select bind:value={sshKeyId} disabled={action.busy}>
          <option value="">— wybierz klucz —</option>
          {#each keys as k (k.id)}
            <option value={k.id}>{k.name} ({k.fingerprint})</option>
          {/each}
        </select>
      </label>

      <label class="remote__checkbox">
        <input type="checkbox" bind:checked={enabled} disabled={action.busy} />
        Auto-push po commicie
      </label>

      <div class="remote__actions">
        <button
          type="submit"
          class="btn-primary"
          disabled={action.busy || !originUrl.trim() || !sshKeyId}
        >
          {action.busy ? '…' : 'Zapisz'}
        </button>

        {#if remote}
          <button type="button" class="btn-ghost" disabled={action.busy} onclick={pushNow}>
            Push teraz
          </button>
          <button type="button" class="btn-danger" disabled={action.busy} onclick={remove}>
            Usuń remote
          </button>
        {/if}
      </div>
    </form>

    {#if remote}
      <div class="remote__status">
        {#if remote.pushed_at}
          <span class="remote__status-item">Ostatni push: {remote.pushed_at}</span>
        {:else}
          <span class="remote__status-item remote__status-item--muted">Brak pushów</span>
        {/if}
        {#if remote.last_error}
          <span class="remote__status-item remote__status-item--error">{remote.last_error}</span>
        {/if}
      </div>
    {/if}
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .remote {
    border-top: 1px solid v.$border;
    padding: v.$space-sm v.$space-lg v.$space-md;

    &__heading {
      font-size: 0.68rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-muted;
      margin: 0 0 v.$space-sm;
    }

    &__hint {
      font-size: 0.75rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      margin: 0;

      a {
        color: v.$accent-dark;
        text-decoration: underline;
      }
    }

    &__form {
      display: flex;
      flex-direction: column;
      gap: v.$space-sm;
    }

    &__error {
      font-size: 0.78rem;
      font-family: v.$font-mono;
      color: v.$error;
      margin: 0;
    }

    &__label {
      display: flex;
      flex-direction: column;
      gap: 3px;
      font-size: 0.68rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-muted;

      input[type='text'],
      select {
        padding: 6px 10px;
        background: v.$bg-surface;
        border: 1px solid v.$border;
        border-radius: v.$radius-md;
        color: v.$text-primary;
        font-size: 0.85rem;
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

      select {
        cursor: pointer;
      }
    }

    &__checkbox {
      display: flex;
      align-items: center;
      gap: v.$space-xs;
      font-size: 0.78rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      cursor: pointer;

      input[type='checkbox'] {
        accent-color: v.$accent;
        width: 14px;
        height: 14px;
        cursor: pointer;

        &:disabled {
          opacity: 0.5;
        }
      }
    }

    &__actions {
      display: flex;
      gap: v.$space-sm;
      flex-wrap: wrap;
      margin-top: v.$space-xs;

      .btn-ghost {
        background: none;
        border: 1px solid v.$border;
        border-radius: v.$radius-md;
        color: v.$text-muted;
        font-size: 0.8rem;
        font-family: v.$font-mono;
        padding: 6px 14px;
        cursor: pointer;
        transition: border-color 0.15s;

        &:hover {
          border-color: v.$text-muted;
        }

        &:disabled {
          opacity: 0.5;
        }
      }

      .btn-danger {
        background: none;
        border: 1px solid v.$border;
        border-radius: v.$radius-md;
        color: v.$error;
        font-size: 0.8rem;
        font-family: v.$font-mono;
        padding: 6px 14px;
        cursor: pointer;
        transition:
          border-color 0.15s,
          color 0.15s;

        &:hover {
          border-color: v.$error;
        }

        &:disabled {
          opacity: 0.5;
        }
      }
    }

    &__status {
      margin-top: v.$space-sm;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    &__status-item {
      font-size: 0.72rem;
      font-family: v.$font-mono;
      color: v.$text-muted;

      &--error {
        color: v.$error;
      }

      &--muted {
        opacity: 0.6;
      }
    }
  }
</style>
