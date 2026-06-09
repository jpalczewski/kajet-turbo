<script lang="ts">
  let { email, onLogout }: { email: string; onLogout: () => void } = $props()
  let open = $state(false)

  function close() { open = false }
</script>

<svelte:window onclick={(e) => { if (!(e.target as Element).closest('.user-menu')) close() }} />

<div class="user-menu">
  <button class="user-menu__trigger" onclick={() => (open = !open)} aria-expanded={open}>
    {email}
    <span class="user-menu__arrow" class:open>▾</span>
  </button>
  {#if open}
    <div class="user-menu__dropdown">
      <a href="/settings" onclick={close} class="user-menu__item">Ustawienia instancji</a>
      <button
        onclick={() => { close(); onLogout() }}
        class="user-menu__item user-menu__item--danger"
      >
        Wyloguj się
      </button>
    </div>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .user-menu { position: relative; }

  .user-menu__trigger {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    padding: 4px 10px;
    background: transparent;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    color: v.$text-muted;
    font-size: 0.75rem;
    font-family: v.$font-mono;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
    &:hover { border-color: v.$border-accent; color: v.$text-secondary; }
  }

  .user-menu__arrow {
    transition: transform 0.15s;
    &.open { transform: rotate(180deg); }
  }

  .user-menu__dropdown {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    min-width: 200px;
    background: v.$bg-surface;
    border: 1px solid v.$border-accent;
    border-radius: v.$radius-lg;
    padding: v.$space-xs;
    z-index: 100;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(240, 184, 0, 0.05);
  }

  .user-menu__item {
    display: block;
    width: 100%;
    padding: v.$space-sm v.$space-md;
    color: v.$text-secondary;
    font-size: 0.75rem;
    font-family: v.$font-mono;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    text-decoration: none;
    background: transparent;
    border: none;
    border-radius: v.$radius-sm;
    text-align: left;
    cursor: pointer;
    transition: background 0.1s, color 0.1s;
    &:hover { background: rgba(240, 184, 0, 0.06); color: v.$text-primary; }
    &--danger:hover { color: v.$error; background: rgba(255, 77, 77, 0.06); }
  }
</style>
