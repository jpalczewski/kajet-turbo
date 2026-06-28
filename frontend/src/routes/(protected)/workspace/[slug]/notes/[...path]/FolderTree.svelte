<script lang="ts">
  import { goto } from '$app/navigation';
  import { SvelteSet } from 'svelte/reactivity';
  import { notesPath } from '$lib/routes';
  import InlineCreateInput from './InlineCreateInput.svelte';
  import FolderMetaDialog from './FolderMetaDialog.svelte';
  import { ancestors, buildTree } from './tree';
  import type { TreeNode } from './tree';

  let {
    folders,
    currentFolder,
    slug,
    onCreateFolder,
  }: {
    folders: string[];
    currentFolder: string;
    slug: string;
    onCreateFolder: (path: string) => Promise<void>;
  } = $props();

  let tree = $derived(buildTree(folders));

  // Auto-expand ancestors of the current folder on mount and on every
  // navigation; manual expand/collapse state is kept otherwise (never
  // auto-collapsed).
  const expanded = new SvelteSet<string>();
  $effect(() => {
    for (const path of ancestors(currentFolder)) expanded.add(path);
  });

  function toggle(path: string) {
    if (expanded.has(path)) {
      expanded.delete(path);
    } else {
      expanded.add(path);
    }
  }

  function navigate(folder: string) {
    goto(notesPath(slug, folder));
  }

  let creatingIn: string | null = $state(null);
  let metaDialog: FolderMetaDialog;
  let editingFolder = $state('');

  function openMeta(path: string, e: MouseEvent) {
    e.stopPropagation();
    editingFolder = path;
    metaDialog.open();
  }

  function startCreating() {
    creatingIn = currentFolder;
    for (const path of ancestors(currentFolder)) expanded.add(path);
  }

  function validateFolderName(name: string): string | null {
    if (!/^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$/.test(name)) {
      return 'Tylko litery, cyfry, kropka, myślnik, ukośnik';
    }
    return null;
  }

  async function submitCreate(parentPath: string, name: string) {
    const fullPath = parentPath ? `${parentPath}/${name}` : name;
    await onCreateFolder(fullPath);
    creatingIn = null;
  }
</script>

{#snippet inlineInput(parentPath: string)}
  {#if creatingIn === parentPath}
    <li class="new-folder-row">
      <span class="folder-chevron"></span>
      <div class="new-folder-box">
        <InlineCreateInput
          variant="tree"
          placeholder="nazwa-folderu"
          validate={validateFolderName}
          onsubmit={(name) => submitCreate(parentPath, name)}
          oncancel={() => (creatingIn = null)}
        />
      </div>
    </li>
  {/if}
{/snippet}

{#snippet node(n: TreeNode)}
  <li>
    <div class="folder-row-wrap">
      <button
        class="folder-row"
        class:active={currentFolder === n.fullPath}
        onclick={() => {
          toggle(n.fullPath);
          navigate(n.fullPath);
        }}
      >
        <span class="folder-chevron">{expanded.has(n.fullPath) ? '▼' : '▶'}</span>
        <span class="folder-name">{n.name}/</span>
      </button>
      <button class="meta-btn" onclick={(e) => openMeta(n.fullPath, e)} title="Edytuj metadane"
        >⚙</button
      >
    </div>
    {#if expanded.has(n.fullPath)}
      <ul class="subtree">
        {#each n.children as child (child.fullPath)}
          {@render node(child)}
        {/each}
        {@render inlineInput(n.fullPath)}
      </ul>
    {/if}
  </li>
{/snippet}

<nav class="folder-tree">
  <div class="tree-header">
    <button
      class="folder-row root-row"
      class:active={currentFolder === ''}
      onclick={() => navigate('')}
    >
      <span class="folder-name">{slug}</span>
    </button>
    <button class="create-btn" onclick={startCreating} title="Nowy folder">+</button>
  </div>
  <ul class="tree-root">
    {#each tree as n (n.fullPath)}
      {@render node(n)}
    {/each}
    {@render inlineInput('')}
  </ul>
</nav>

<FolderMetaDialog bind:this={metaDialog} {slug} folder={editingFolder} />

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .folder-tree {
    font-family: v.$font-mono;
    font-size: 0.82rem;
    overflow-y: auto;
    height: 100%;
  }

  ul {
    list-style: none;
    padding: 0;
    margin: 0;
  }

  .subtree {
    padding-left: 12px;
  }

  .tree-header {
    display: flex;
    align-items: center;
    padding-right: 8px;
    margin-bottom: 4px;
  }

  .folder-row {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    padding: 3px 12px;
    background: none;
    border: none;
    color: v.$text-muted;
    cursor: pointer;
    text-align: left;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;

    &:hover {
      color: v.$text-primary;
    }
    &.active {
      color: v.$accent;
    }
  }

  .root-row {
    flex: 1;
    padding: 4px 12px;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: v.$text-muted;
  }

  .folder-chevron {
    font-size: 0.6rem;
    width: 10px;
    flex-shrink: 0;
  }

  .create-btn {
    background: none;
    border: none;
    color: v.$accent;
    font-size: 1.1rem;
    line-height: 1;
    padding: 0 6px;
    cursor: pointer;
    flex-shrink: 0;

    &:hover {
      color: v.$accent-hover;
    }
  }

  .folder-row-wrap {
    display: flex;
    align-items: center;

    .folder-row {
      flex: 1;
      min-width: 0;
    }

    .meta-btn {
      display: none;
      background: none;
      border: none;
      color: v.$text-muted;
      font-size: 0.75rem;
      line-height: 1;
      padding: 0 6px;
      cursor: pointer;
      flex-shrink: 0;

      &:hover {
        color: v.$accent;
      }
    }

    &:hover .meta-btn {
      display: block;
    }
  }

  .new-folder-row {
    display: flex;
    align-items: flex-start;
    gap: 4px;
    padding: 3px 12px;
  }

  .new-folder-box {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
</style>
