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
    padding: 5px 10px;
    background: transparent;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    color: v.$text-secondary;
    font-size: 0.875rem;
    cursor: pointer;
    &:hover { border-color: v.$text-muted; color: v.$text-primary; }
  }

  .user-menu__arrow {
    transition: transform 0.15s;
    &.open { transform: rotate(180deg); }
  }

  .user-menu__dropdown {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    min-width: 200px;
    background: v.$bg-surface;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    padding: v.$space-xs;
    z-index: 100;
  }

  .user-menu__item {
    display: block;
    width: 100%;
    padding: v.$space-sm v.$space-md;
    color: v.$text-secondary;
    font-size: 0.875rem;
    text-decoration: none;
    background: transparent;
    border: none;
    border-radius: v.$radius-sm;
    text-align: left;
    cursor: pointer;
    &:hover { background: v.$bg-deep; color: v.$text-primary; }
    &--danger:hover { color: v.$error; }
  }
</style>
