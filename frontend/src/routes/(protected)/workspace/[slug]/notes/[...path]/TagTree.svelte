<script lang="ts">
  import { goto } from '$app/navigation';
  import { SvelteSet } from 'svelte/reactivity';
  import type { TagNode } from '$lib/api';
  import { tagsPath } from '$lib/routes';
  import { buildTagTree, tagAncestors, type TagTreeNode } from './tagTree';

  let {
    tags,
    currentTag,
    includeDescendants,
    slug,
  }: {
    tags: TagNode[];
    currentTag: string;
    includeDescendants: boolean;
    slug: string;
  } = $props();

  let tree = $derived(buildTagTree(tags));

  // Auto-expand ancestors of the current tag on navigation; manual expand/collapse
  // state is otherwise kept (never auto-collapsed), mirroring FolderTree.
  const expanded = new SvelteSet<string>();
  $effect(() => {
    for (const path of tagAncestors(currentTag)) expanded.add(path);
  });

  function toggle(path: string) {
    if (expanded.has(path)) expanded.delete(path);
    else expanded.add(path);
  }

  function navigate(path: string) {
    // eslint-disable-next-line svelte/no-navigation-without-resolve
    goto(tagsPath(slug, path, includeDescendants));
  }
</script>

{#snippet node(n: TagTreeNode)}
  <li>
    <button
      class="tag-row"
      class:active={currentTag === n.fullPath}
      onclick={() => {
        toggle(n.fullPath);
        navigate(n.fullPath);
      }}
    >
      <span class="tag-chevron">
        {n.children.length ? (expanded.has(n.fullPath) ? '▼' : '▶') : ''}
      </span>
      <span class="tag-name">#{n.name}</span>
      <span class="tag-count">{n.descendantCount}</span>
    </button>
    {#if expanded.has(n.fullPath) && n.children.length}
      <ul class="subtree">
        {#each n.children as child (child.fullPath)}
          {@render node(child)}
        {/each}
      </ul>
    {/if}
  </li>
{/snippet}

<nav class="tag-tree">
  {#if tree.length === 0}
    <p class="tag-empty">Brak tagów.</p>
  {:else}
    <ul class="tree-root">
      {#each tree as n (n.fullPath)}
        {@render node(n)}
      {/each}
    </ul>
  {/if}
</nav>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .tag-tree {
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
  .tag-row {
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
    &:hover {
      color: v.$text-primary;
    }
    &.active {
      color: v.$accent;
    }
  }
  .tag-chevron {
    font-size: 0.6rem;
    width: 10px;
    flex-shrink: 0;
  }
  .tag-name {
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tag-count {
    font-size: 0.68rem;
    color: v.$accent-dark;
  }
  .tag-empty {
    color: v.$text-muted;
    padding: 12px;
    font-size: 0.8rem;
  }
</style>
