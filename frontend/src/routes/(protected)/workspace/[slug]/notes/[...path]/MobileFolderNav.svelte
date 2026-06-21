<script lang="ts">
  import { notesPath, workspaceSettingsPath } from '$lib/routes';
  import { breadcrumbCrumbs } from '$lib/breadcrumb';
  import { childFolders } from './tree';
  import ExplorerModeToggle from './ExplorerModeToggle.svelte';

  let {
    slug,
    mode,
    folderPath,
    folders,
  }: {
    slug: string;
    mode: 'files' | 'tags';
    folderPath: string;
    folders: string[];
  } = $props();

  const crumbs = $derived(breadcrumbCrumbs(folderPath));
  const subfolders = $derived(childFolders(folders, folderPath));
</script>

<div class="mobile-folder-nav">
  <ExplorerModeToggle {slug} {mode} />

  {#if mode === 'files'}
    <nav class="crumbs">
      <a class="crumb" href={notesPath(slug)}>{slug}</a>
      {#each crumbs as crumb (crumb.folder)}
        <span class="crumb-sep">/</span>
        <a class="crumb" href={notesPath(slug, crumb.folder)}>{crumb.label}</a>
      {/each}
    </nav>

    {#if subfolders.length}
      <ul class="subfolders">
        {#each subfolders as folder (folder)}
          <li>
            <a class="subfolder" href={notesPath(slug, folder)}>
              <span class="subfolder__icon">📁</span>
              <span class="subfolder__name">{folder.split('/').at(-1)}/</span>
              <span class="subfolder__chevron">›</span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}

  <a class="settings" href={workspaceSettingsPath(slug)}>⚙ Ustawienia</a>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  // Mobile-only: the desktop sidebar provides this navigation on wide screens.
  .mobile-folder-nav {
    display: none;

    @include bp.mobile {
      display: block;
      border-bottom: 1px solid v.$border;
    }
  }

  .crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: v.$space-xs;
    padding: 0 12px 8px;
    font-family: v.$font-mono;
    font-size: 0.72rem;
  }
  .crumb {
    color: v.$accent-dark;
    text-decoration: none;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .crumb-sep {
    color: v.$text-muted;
  }

  .subfolders {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .subfolder {
    display: flex;
    align-items: center;
    gap: v.$space-sm;
    min-height: 44px;
    padding: 0 12px;
    border-top: 1px solid v.$border;
    color: v.$text-secondary;
    font-family: v.$font-mono;
    font-size: 0.85rem;
    text-decoration: none;

    &__name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    &__chevron {
      color: v.$text-muted;
    }
  }

  .settings {
    display: block;
    padding: 10px 12px;
    border-top: 1px solid v.$border;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$text-muted;
    text-decoration: none;
  }
</style>
