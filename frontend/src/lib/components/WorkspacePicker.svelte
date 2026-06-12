<script lang="ts">
  import { notesPath, workspacesPath } from '$lib/routes';

  let { slug, workspaces = [] }: { slug?: string; workspaces: string[] } = $props();
  let open = $state(false);
</script>

{#if slug}
  <div class="workspace-picker">
    <button class="workspace-picker__trigger" onclick={() => (open = !open)} aria-expanded={open}>
      {slug}
      <span class="workspace-picker__arrow" class:open>▾</span>
    </button>
    {#if open}
      <div class="workspace-picker__dropdown">
        {#each workspaces as ws (ws)}
          <a
            href={notesPath(ws)}
            onclick={() => (open = false)}
            class="workspace-picker__item"
            class:workspace-picker__item--active={ws === slug}
          >
            {ws}
          </a>
        {/each}
        {#if workspaces.length > 0}
          <div class="workspace-picker__divider"></div>
        {/if}
        <a href={workspacesPath()} onclick={() => (open = false)} class="workspace-picker__manage">
          Zarządzaj workspaceami
        </a>
      </div>
    {/if}
  </div>
{:else}
  <a href={workspacesPath()} class="workspace-picker workspace-picker--empty">
    Wybierz workspace ▾
  </a>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .workspace-picker {
    position: relative;

    &--empty {
      color: v.$text-muted;
      font-size: 0.75rem;
      font-family: v.$font-mono;
      text-decoration: none;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 4px 10px;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      transition:
        border-color 0.15s,
        color 0.15s;
      &:hover {
        color: v.$accent;
        border-color: v.$accent-dark;
      }
    }
  }

  .workspace-picker__trigger {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    padding: 4px 10px;
    background: transparent;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    color: v.$accent;
    font-size: 0.75rem;
    font-family: v.$font-mono;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    cursor: pointer;
    transition:
      border-color 0.15s,
      box-shadow 0.15s;
    &:hover {
      border-color: v.$accent-dark;
      box-shadow: 0 0 6px rgba(240, 184, 0, 0.12);
    }
  }

  .workspace-picker__arrow {
    color: v.$accent-dark;
    transition: transform 0.15s;
    &.open {
      transform: rotate(180deg);
    }
  }

  .workspace-picker__dropdown {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    min-width: 200px;
    background: v.$bg-surface;
    border: 1px solid v.$border-accent;
    border-radius: v.$radius-lg;
    padding: v.$space-xs;
    z-index: 100;
    box-shadow:
      0 4px 24px rgba(0, 0, 0, 0.6),
      0 0 0 1px rgba(240, 184, 0, 0.05);
  }

  .workspace-picker__item {
    display: block;
    padding: v.$space-sm v.$space-md;
    color: v.$text-secondary;
    font-size: 0.75rem;
    font-family: v.$font-mono;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    text-decoration: none;
    border-radius: v.$radius-sm;
    transition:
      background 0.1s,
      color 0.1s;

    &:hover {
      background: rgba(240, 184, 0, 0.06);
      color: v.$accent;
    }

    &--active {
      color: v.$accent;
      background: rgba(240, 184, 0, 0.08);
    }
  }

  .workspace-picker__divider {
    height: 1px;
    background: v.$border;
    margin: v.$space-xs v.$space-sm;
  }

  .workspace-picker__manage {
    display: block;
    padding: v.$space-sm v.$space-md;
    color: v.$text-muted;
    font-size: 0.75rem;
    font-family: v.$font-mono;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    text-decoration: none;
    border-radius: v.$radius-sm;
    transition:
      background 0.1s,
      color 0.1s;
    &:hover {
      background: rgba(240, 184, 0, 0.06);
      color: v.$accent;
    }
  }
</style>
