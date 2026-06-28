<script lang="ts">
  import { invalidate } from '$app/navigation';
  import { page } from '$app/state';
  import { apiUpdateWorkspaceApiWorkspacesNamePatch } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import { notesPath } from '$lib/routes';
  import { formatUnixDate } from '$lib/utils/format';
  import { groupWorkspaces } from '$lib/utils/groupWorkspaces';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
  import CreateWorkspaceForm from './CreateWorkspaceForm.svelte';
  import WorkspaceRemote from './WorkspaceRemote.svelte';

  let workspaces = $derived(page.data.workspaces ?? []);
  let groups = $derived(groupWorkspaces(workspaces));

  // Per-card edit state keyed by workspace name
  let editing = $state<Record<string, boolean>>({});
  let editDesc = $state<Record<string, string>>({});
  let editFolder = $state<Record<string, string>>({});
  let editTags = $state<Record<string, string>>({});
  let editError = $state<Record<string, string>>({});
  let saving = $state<Record<string, boolean>>({});

  function startEdit(ws: { name: string; description?: string; folder?: string; tags?: string[] }) {
    editDesc[ws.name] = ws.description ?? '';
    editFolder[ws.name] = ws.folder ?? '';
    editTags[ws.name] = (ws.tags ?? []).join(', ');
    editError[ws.name] = '';
    editing[ws.name] = true;
  }

  function cancelEdit(name: string) {
    editing[name] = false;
  }

  async function saveEdit(name: string) {
    saving[name] = true;
    editError[name] = '';
    const rawTags = editTags[name] ?? '';
    const tags = rawTags
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
    try {
      await apiUpdateWorkspaceApiWorkspacesNamePatch(
        name,
        jsonBody({
          description: editDesc[name] ?? '',
          folder: editFolder[name] ?? '',
          tags,
        }),
      );
      editing[name] = false;
      await invalidate('app:workspaces');
    } catch (e) {
      editError[name] = apiErrorMessage(e, 'Save failed.');
    } finally {
      saving[name] = false;
    }
  }
</script>

<main class="page">
  <header class="page__header">
    <h1>Workspaces</h1>
    <span class="page__count">{workspaces.length}</span>
  </header>

  <CreateWorkspaceForm />

  {#if workspaces.length === 0}
    <EmptyState>Brak workspace'ów. Utwórz pierwszy powyżej.</EmptyState>
  {:else}
    {#each groups as group (group.folder)}
      {#if group.folder}
        <h2 class="folder-heading">{group.folder}</h2>
      {/if}
      <ul class="ws-list">
        {#each group.items as ws (ws.name)}
          <li class="ws-card">
            <a href={notesPath(ws.name)} class="ws-card__link">
              <span class="ws-card__name">{ws.name}</span>
              <span class="ws-card__meta">
                <span class="ws-card__count"
                  >{ws.file_count}
                  {ws.file_count === 1
                    ? 'notatka'
                    : ws.file_count < 5
                      ? 'notatki'
                      : 'notatek'}</span
                >
                {#if ws.last_commit_at}
                  <span class="ws-card__sep">·</span>
                  <span class="ws-card__date">{formatUnixDate(ws.last_commit_at)}</span>
                {/if}
              </span>
              <span class="ws-card__arrow">→</span>
            </a>
            {#if ws.description}
              <span class="ws-card__desc">{ws.description}</span>
            {/if}
            {#if ws.tags && ws.tags.length}
              <div class="ws-card__tags">
                {#each ws.tags as tag (tag)}
                  <span class="ws-card__tag">#{tag}</span>
                {/each}
              </div>
            {/if}

            {#if editing[ws.name]}
              <form
                class="ws-card__edit-form"
                onsubmit={(e) => {
                  e.preventDefault();
                  saveEdit(ws.name);
                }}
              >
                {#if editError[ws.name]}
                  <p class="ws-card__edit-error">{editError[ws.name]}</p>
                {/if}
                <label class="ws-card__edit-label">
                  Description
                  <input
                    type="text"
                    bind:value={editDesc[ws.name]}
                    placeholder="Short description…"
                    disabled={saving[ws.name]}
                  />
                </label>
                <label class="ws-card__edit-label">
                  Folder
                  <input
                    type="text"
                    bind:value={editFolder[ws.name]}
                    placeholder="e.g. Work"
                    disabled={saving[ws.name]}
                  />
                </label>
                <label class="ws-card__edit-label">
                  Tags (comma-separated)
                  <input
                    type="text"
                    bind:value={editTags[ws.name]}
                    placeholder="e.g. personal, archive"
                    disabled={saving[ws.name]}
                  />
                </label>
                <div class="ws-card__edit-actions">
                  <button type="submit" class="btn-primary" disabled={saving[ws.name]}>
                    {saving[ws.name] ? '…' : 'Save'}
                  </button>
                  <button
                    type="button"
                    class="btn-ghost"
                    disabled={saving[ws.name]}
                    onclick={() => cancelEdit(ws.name)}>Cancel</button
                  >
                </div>
              </form>
            {:else}
              <button class="ws-card__edit-btn" onclick={() => startEdit(ws)}>edit</button>
            {/if}

            <WorkspaceRemote name={ws.name} keys={page.data.keys ?? []} />
          </li>
        {/each}
      </ul>
    {/each}
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

  .folder-heading {
    font-size: 0.72rem;
    font-family: v.$font-mono;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: v.$text-muted;
    margin: v.$space-xl 0 v.$space-sm;

    &:first-of-type {
      margin-top: v.$space-lg;
    }
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
    position: relative;

    &:hover {
      border-color: v.$accent-dark;
    }

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

    &__meta {
      display: flex;
      align-items: center;
      gap: v.$space-xs;
      margin-left: v.$space-md;
      flex: 1;
    }

    &__count,
    &__date {
      font-size: 0.72rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      letter-spacing: 0.03em;
    }

    &__sep {
      font-size: 0.72rem;
      color: v.$text-muted;
      opacity: 0.5;
    }

    &__arrow {
      color: v.$accent-dark;
      font-size: 0.85rem;
      transition:
        color 0.15s,
        transform 0.15s;
    }

    &:hover &__arrow {
      color: v.$accent;
      transform: translateX(3px);
    }

    &__desc {
      display: block;
      font-size: 0.78rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
      padding: 0 v.$space-lg v.$space-sm;
      line-height: 1.4;
    }

    &__tags {
      display: flex;
      flex-wrap: wrap;
      gap: v.$space-xs;
      padding: 0 v.$space-lg v.$space-sm;
    }

    &__tag {
      font-size: 0.68rem;
      font-family: v.$font-mono;
      color: v.$accent-dark;
      background: rgba(240, 184, 0, 0.06);
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 6px;
      letter-spacing: 0.03em;
    }

    &__edit-btn {
      position: absolute;
      top: v.$space-md;
      right: 2.2rem;
      font-size: 0.65rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-muted;
      background: none;
      border: none;
      cursor: pointer;
      padding: 2px 4px;
      opacity: 0;
      transition: opacity 0.15s;

      // Reveal on pointer hover (fine pointer / mouse)
      @media (hover: hover) {
        .ws-card:hover & {
          opacity: 1;
        }
      }

      // Reveal on keyboard focus: card focus-within or button focus-visible
      .ws-card:focus-within & {
        opacity: 1;
      }

      &:focus-visible {
        opacity: 1;
        outline: 2px solid v.$accent;
        outline-offset: 2px;
        border-radius: v.$radius-sm;
      }

      // Always visible on touch / coarse-pointer devices where hover never fires
      @media (hover: none) {
        opacity: 0.5;
      }
    }

    &__edit-form {
      padding: v.$space-sm v.$space-lg v.$space-md;
      display: flex;
      flex-direction: column;
      gap: v.$space-sm;
      border-top: 1px solid v.$border;
      margin-top: v.$space-xs;
    }

    &__edit-error {
      font-size: 0.78rem;
      font-family: v.$font-mono;
      color: v.$error;
      margin: 0;
    }

    &__edit-label {
      display: flex;
      flex-direction: column;
      gap: 3px;
      font-size: 0.68rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-muted;

      input {
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
    }

    &__edit-actions {
      display: flex;
      gap: v.$space-sm;
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
    }
  }
</style>
