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
    padding: v.$space-md v.$space-lg;
    border-bottom: 1px solid v.$border;
    background: v.$bg-deep;
  }

  .navbar__logo {
    font-size: 1rem;
    font-weight: 700;
    color: v.$text-primary;
    text-decoration: none;
    &:hover { color: v.$accent-light; }
  }

  .navbar__center {
    display: flex;
    align-items: center;
    gap: v.$space-md;
    margin-right: auto;
    margin-left: v.$space-md;
  }

  .navbar__link {
    color: v.$text-secondary;
    font-size: 0.875rem;
    text-decoration: none;
    padding: 5px 10px;
    border-radius: v.$radius-md;
    transition: color 0.15s;
    &:hover { color: v.$text-primary; }
    &--active { color: v.$text-primary; background: v.$bg-surface; }
  }
</style>
