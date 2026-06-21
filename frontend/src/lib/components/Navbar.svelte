<script lang="ts">
  import { page } from '$app/state';
  import { goto, invalidate } from '$app/navigation';
  import { apiSessionDeleteApiSessionDelete } from '$lib/api';
  import { homePath, notesPath } from '$lib/routes';
  import WorkspacePicker from './WorkspacePicker.svelte';
  import UserMenu from './UserMenu.svelte';

  const slug = $derived((page.params as Record<string, string>).slug as string | undefined);

  const notesActive = $derived(!!slug && page.url.pathname.startsWith(`/workspace/${slug}/note`));

  async function handleLogout() {
    await apiSessionDeleteApiSessionDelete({ credentials: 'include' });
    await invalidate('app:session');
    await goto(homePath());
  }
</script>

<nav class="navbar">
  <a href={homePath()} class="navbar__logo">kajet-turbo</a>

  {#if page.data.session}
    <div class="navbar__center">
      <WorkspacePicker {slug} workspaces={(page.data.workspaces ?? []).map((w) => w.name)} />
      {#if slug}
        <a href={notesPath(slug)} class="navbar__link" class:navbar__link--active={notesActive}>
          Notes
        </a>
      {/if}
    </div>
    <UserMenu email={page.data.session.email} onLogout={handleLogout} />
  {/if}
</nav>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

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
      min-width: 0;
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
    }
  }
</style>
