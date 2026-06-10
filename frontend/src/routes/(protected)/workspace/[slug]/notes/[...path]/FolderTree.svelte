<script lang="ts">
  import { goto } from '$app/navigation'

  let { folders, currentFolder, slug, onCreateFolder }: {
    folders: string[]
    currentFolder: string
    slug: string
    onCreateFolder: (path: string) => Promise<void>
  } = $props()

  type TreeNode = { name: string; fullPath: string; children: TreeNode[] }

  function buildTree(paths: string[]): TreeNode[] {
    const root: TreeNode[] = []
    const map = new Map<string, TreeNode>()
    for (const path of [...paths].sort()) {
      const parts = path.split('/')
      const name = parts.at(-1)!
      const node: TreeNode = { name, fullPath: path, children: [] }
      map.set(path, node)
      const parentPath = parts.slice(0, -1).join('/')
      if (parentPath && map.has(parentPath)) {
        map.get(parentPath)!.children.push(node)
      } else {
        root.push(node)
      }
    }
    return root
  }

  let tree = $derived(buildTree(folders))
  let expandedOverride = $state<Set<string> | null>(null)
  let expanded = $derived(
    expandedOverride ?? new Set<string>(
      currentFolder
        ? currentFolder.split('/').map((_, i, arr) => arr.slice(0, i + 1).join('/'))
        : []
    )
  )

  function toggle(path: string) {
    const next = new Set(expanded)
    next.has(path) ? next.delete(path) : next.add(path)
    expandedOverride = next
  }

  function navigate(folder: string) {
    goto(`/workspace/${slug}/notes/${folder}`)
  }

  let creatingIn: string | null = $state(null)
  let newFolderInput = $state('')
  let createError = $state('')

  function startCreating() {
    creatingIn = currentFolder
    newFolderInput = ''
    createError = ''
    if (currentFolder) {
      const next = new Set(expanded)
      currentFolder.split('/').forEach((_, i, arr) => next.add(arr.slice(0, i + 1).join('/')))
      expandedOverride = next
    }
  }

  async function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      creatingIn = null
      return
    }
    if (e.key !== 'Enter') return
    const name = newFolderInput.trim()
    if (!name) return
    if (!/^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$/.test(name)) {
      createError = 'Tylko litery, cyfry, kropka, myślnik, ukośnik'
      return
    }
    const fullPath = creatingIn ? `${creatingIn}/${name}` : name
    try {
      await onCreateFolder(fullPath)
      creatingIn = null
    } catch (err: unknown) {
      createError = err instanceof Error ? err.message : 'Błąd'
    }
  }
</script>

{#snippet inlineInput(parentPath: string)}
  {#if creatingIn === parentPath}
    <li class="new-folder-row">
      <span class="folder-chevron"></span>
      <input
        class="new-folder-input"
        class:new-folder-input--error={!!createError}
        bind:value={newFolderInput}
        onkeydown={handleKeydown}
        placeholder="nazwa-folderu"
        autofocus
      />
    </li>
    {#if createError}
      <li class="new-folder-error">{createError}</li>
    {/if}
  {/if}
{/snippet}

{#snippet node(n: TreeNode)}
  <li>
    <button
      class="folder-row"
      class:active={currentFolder === n.fullPath}
      onclick={() => { toggle(n.fullPath); navigate(n.fullPath) }}
    >
      <span class="folder-chevron">{expanded.has(n.fullPath) ? '▼' : '▶'}</span>
      <span class="folder-name">{n.name}/</span>
    </button>
    {#if expanded.has(n.fullPath)}
      <ul class="subtree">
        {#each n.children as child}
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
    {#each tree as n}
      {@render node(n)}
    {/each}
    {@render inlineInput('')}
  </ul>
</nav>

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

    &:hover { color: v.$text-primary; }
    &.active { color: v.$accent; }
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

    &:hover { color: v.$accent-hover; }
  }

  .new-folder-row {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 12px;
  }

  .new-folder-input {
    background: v.$bg-raised;
    border: 1px solid v.$accent;
    color: v.$text-primary;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    padding: 1px 5px;
    outline: none;
    border-radius: v.$radius-sm;
    width: 120px;

    &--error { border-color: v.$error; }
  }

  .new-folder-error {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$error;
    padding: 1px 12px 3px 22px;
  }
</style>
