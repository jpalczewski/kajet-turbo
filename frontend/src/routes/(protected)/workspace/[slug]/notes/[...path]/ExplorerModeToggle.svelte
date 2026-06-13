<script lang="ts">
  import { goto } from '$app/navigation';
  import { notesPath, tagsPath } from '$lib/routes';

  let { slug, mode }: { slug: string; mode: 'files' | 'tags' } = $props();
</script>

<div class="mode-toggle">
  <button class="mode-btn" class:active={mode === 'files'} onclick={() => goto(notesPath(slug))}>
    Pliki
  </button>
  <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
  <button class="mode-btn" class:active={mode === 'tags'} onclick={() => goto(tagsPath(slug))}>
    Tagi
  </button>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .mode-toggle {
    display: flex;
    gap: 2px;
    padding: 4px 8px 8px;
  }
  .mode-btn {
    flex: 1;
    background: none;
    border: 1px solid v.$border;
    color: v.$text-muted;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    padding: 4px 0;
    cursor: pointer;
    &.active {
      color: v.$accent;
      border-color: v.$accent;
    }
    &:hover {
      color: v.$text-primary;
    }
  }
</style>
