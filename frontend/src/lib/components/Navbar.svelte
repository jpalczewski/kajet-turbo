<script lang="ts">
  import { page } from '$app/state';
  import { goto, invalidate } from '$app/navigation';
  import { apiSessionDeleteApiSessionDelete } from '$lib/api';
  import { collectionsPath, homePath, notesPath, recentPath } from '$lib/routes';
  import WorkspaceGraphLink from './WorkspaceGraphLink.svelte';
  import WorkspacePicker from './WorkspacePicker.svelte';
  import UserMenu from './UserMenu.svelte';

  let center = $state<HTMLElement>();

  const slug = $derived((page.params as Record<string, string>).slug as string | undefined);

  const notesActive = $derived(!!slug && page.url.pathname.startsWith(`/workspace/${slug}/note`));

  const recentActive = $derived(
    !!slug && page.url.pathname.startsWith(`/workspace/${slug}/recent`),
  );

  const collectionsActive = $derived(
    !!slug && page.url.pathname.startsWith(`/workspace/${slug}/collections`),
  );

  // On a narrow screen the tab strip scrolls sideways; keep the current tab in view.
  $effect(() => {
    void page.url.pathname;
    center
      ?.querySelector('[aria-current="page"], .navbar__link--active')
      ?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  });

  async function handleLogout() {
    await apiSessionDeleteApiSessionDelete({ credentials: 'include' });
    await invalidate('app:session');
    await goto(homePath());
  }
</script>

<nav class="navbar">
  <a href={homePath()} class="navbar__logo">kajet-turbo</a>

  {#if page.data.session}
    <div class="navbar__center" bind:this={center}>
      <WorkspacePicker {slug} workspaces={(page.data.workspaces ?? []).map((w) => w.name)} />
      {#if slug}
        <a href={notesPath(slug)} class="navbar__link" class:navbar__link--active={notesActive}>
          Notes
        </a>
        <a href={recentPath(slug)} class="navbar__link" class:navbar__link--active={recentActive}>
          Ostatnie
        </a>
        <a
          href={collectionsPath(slug)}
          class="navbar__link"
          class:navbar__link--active={collectionsActive}
        >
          Kolekcje
        </a>
        <WorkspaceGraphLink {slug} variant="navbar" />
      {/if}
    </div>
    <UserMenu email={page.data.session.email} onLogout={handleLogout} />
  {/if}
</nav>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;
  @use '$lib/styles/effects' as fx;

  .navbar {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    padding: 0 max(v.$space-lg, env(safe-area-inset-right)) 0
      max(v.$space-lg, env(safe-area-inset-left));
    height: 48px;
    border-bottom: 1px solid v.$border;
    background: rgba(8, 8, 8, 0.95);
    backdrop-filter: blur(4px);
    position: sticky;
    top: 0;
    z-index: 50;

    @include bp.mobile {
      gap: v.$space-sm;
      padding: 0 max(v.$space-md, env(safe-area-inset-right)) 0
        max(v.$space-md, env(safe-area-inset-left));
    }
  }

  .navbar__logo {
    font-size: 0.9rem;
    font-weight: 700;
    font-family: v.$font-mono;
    text-decoration: none;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    background: v.$gradient-accent;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    transition: filter 0.15s;
    @include fx.text-glow(v.$accent, 0.35);
    &:hover {
      filter: brightness(1.2);
    }
  }

  .navbar__center {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    margin-right: auto;
    margin-left: v.$space-md;

    @include bp.mobile {
      gap: v.$space-sm;
      margin-left: v.$space-sm;
      // Four tabs do not fit next to the picker and the user menu at phone width, so the
      // strip takes the leftover space and scrolls instead of overlapping its neighbours.
      flex: 1 1 0;
      min-width: 0;
      overflow-x: auto;
      scrollbar-width: none;

      &::-webkit-scrollbar {
        display: none;
      }

      > :global(*) {
        flex-shrink: 0;
        white-space: nowrap;
      }
    }
  }

  .navbar__link {
    color: v.$text-muted;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    text-decoration: none;
    padding: 4px 10px;
    border-radius: v.$radius-md;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    transition: color 0.15s;
    &:hover {
      color: v.$text-secondary;
    }
    &--active {
      color: v.$accent;
      background: rgba(240, 184, 0, 0.08);
      border: 1px solid rgba(240, 184, 0, 0.15);
      @include fx.glow(v.$accent, 0.25);
    }
  }
</style>
