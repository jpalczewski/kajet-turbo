<script lang="ts">
  import { page } from '$app/state'
  import { goto, invalidateAll } from '$app/navigation'
  import WorkspacePicker from './WorkspacePicker.svelte'
  import UserMenu from './UserMenu.svelte'

  const slug = $derived((page.params as Record<string, string>).slug as string | undefined)

  const notesActive = $derived(
    !!slug && page.url.pathname.startsWith(`/workspace/${slug}/note`)
  )

  async function handleLogout() {
    await fetch('/api/session', { method: 'DELETE', credentials: 'include' })
    await invalidateAll()
    await goto('/')
  }
</script>

<nav class="navbar">
  <a href="/" class="navbar__logo">kajet-turbo</a>

  {#if page.data.session}
    <div class="navbar__center">
      <WorkspacePicker {slug} />
      {#if slug}
        <a
          href="/workspace/{slug}/notes"
          class="navbar__link"
          class:navbar__link--active={notesActive}
        >
          Notes
        </a>
      {/if}
    </div>
    <UserMenu email={page.data.session.email} onLogout={handleLogout} />
  {/if}
</nav>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .navbar {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    padding: 0 v.$space-lg;
    height: 48px;
    border-bottom: 1px solid v.$border;
    background: rgba(8, 8, 8, 0.95);
    backdrop-filter: blur(4px);
    position: sticky;
    top: 0;
    z-index: 50;
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
    &:hover { filter: brightness(1.2); }
  }

  .navbar__center {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    margin-right: auto;
    margin-left: v.$space-md;
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
    &:hover { color: v.$text-secondary; }
    &--active {
      color: v.$accent;
      background: rgba(240, 184, 0, 0.08);
      border: 1px solid rgba(240, 184, 0, 0.15);
    }
  }
</style>
