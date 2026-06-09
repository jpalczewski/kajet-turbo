<script lang="ts">
  let { slug }: { slug?: string } = $props()
  let open = $state(false)
</script>

{#if slug}
  <div class="workspace-picker">
    <button
      class="workspace-picker__trigger"
      onclick={() => (open = !open)}
      aria-expanded={open}
    >
      {slug}
      <span class="workspace-picker__arrow" class:open>▾</span>
    </button>
    {#if open}
      <div class="workspace-picker__dropdown">
        <a href="/workspaces" onclick={() => (open = false)} class="workspace-picker__manage">
          Zarządzaj workspaceami
        </a>
      </div>
    {/if}
  </div>
{:else}
  <a href="/workspaces" class="workspace-picker workspace-picker--empty">
    Wybierz workspace ▾
  </a>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .workspace-picker {
    position: relative;

    &--empty {
      color: v.$text-muted;
      font-size: 0.875rem;
      text-decoration: none;
      padding: 5px 10px;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      &:hover { color: v.$text-secondary; border-color: v.$text-muted; }
    }
  }

  .workspace-picker__trigger {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    padding: 5px 10px;
    background: v.$bg-surface;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    color: v.$text-primary;
    font-size: 0.875rem;
    cursor: pointer;
    &:hover { border-color: v.$accent-light; }
  }

  .workspace-picker__arrow {
    transition: transform 0.15s;
    &.open { transform: rotate(180deg); }
  }

  .workspace-picker__dropdown {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    min-width: 180px;
    background: v.$bg-surface;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    padding: v.$space-xs;
    z-index: 100;
  }

  .workspace-picker__manage {
    display: block;
    padding: v.$space-sm v.$space-md;
    color: v.$text-secondary;
    font-size: 0.875rem;
    text-decoration: none;
    border-radius: v.$radius-sm;
    &:hover { background: v.$bg-deep; color: v.$text-primary; }
  }
</style>
