<script lang="ts">
  import { goto } from '$app/navigation'

  let { folders, currentFolder, slug }: {
    folders: string[]
    currentFolder: string
    slug: string
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
</script>

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
    {#if expanded.has(n.fullPath) && n.children.length > 0}
      <ul class="subtree">
        {#each n.children as child}
          {@render node(child)}
        {/each}
      </ul>
    {/if}
  </li>
{/snippet}

<nav class="folder-tree">
  <button
    class="folder-row root-row"
    class:active={currentFolder === ''}
    onclick={() => navigate('')}
  >
    <span class="folder-name">{slug}</span>
  </button>
  <ul class="tree-root">
    {#each tree as n}
      {@render node(n)}
    {/each}
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
    padding: 4px 12px;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: v.$text-muted;
    margin-bottom: 4px;
  }

  .folder-chevron {
    font-size: 0.6rem;
    width: 10px;
    flex-shrink: 0;
  }
</style>
