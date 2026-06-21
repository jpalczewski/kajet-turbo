<script lang="ts">
  import { notesPath, workspacesPath } from '$lib/routes';
  import { breadcrumbCrumbs } from '$lib/breadcrumb';

  let {
    slug,
    folder = null,
    current = '',
  }: { slug: string; folder?: string | null; current?: string } = $props();

  const crumbs = $derived(breadcrumbCrumbs(folder ?? ''));
</script>

<nav class="breadcrumb">
  <a href={workspacesPath()} class="breadcrumb__link">Workspaces</a>
  <span class="breadcrumb__sep">/</span>
  <a href={notesPath(slug)} class="breadcrumb__link">{slug}</a>
  {#each crumbs as crumb (crumb.folder)}
    <span class="breadcrumb__sep">/</span>
    <span class="breadcrumb__folder">{crumb.label}</span>
  {/each}
  {#if current}
    <span class="breadcrumb__sep">/</span>
    <span class="breadcrumb__current">{current}</span>
  {/if}
</nav>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .breadcrumb {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    margin-bottom: v.$space-lg;
    font-size: 0.75rem;
    font-family: v.$font-mono;

    &__link {
      color: v.$accent-dark;
      text-decoration: none;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      transition: color 0.15s;

      &:hover {
        color: v.$accent;
      }
    }

    &__sep {
      color: v.$text-muted;
    }

    &__folder {
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    &__current {
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 200px;
    }
  }
</style>
