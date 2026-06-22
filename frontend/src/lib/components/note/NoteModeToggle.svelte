<script lang="ts">
  let {
    mode,
    onchange,
  }: {
    mode: 'content' | 'chunks';
    onchange: (m: 'content' | 'chunks') => void;
  } = $props();
</script>

<div class="toggle" role="group" aria-label="Widok notatki">
  <button class="toggle__btn" class:active={mode === 'content'} onclick={() => onchange('content')}>
    Treść
  </button>
  <button class="toggle__btn" class:active={mode === 'chunks'} onclick={() => onchange('chunks')}>
    Chunki
  </button>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .toggle {
    display: flex;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    overflow: hidden;
    flex-shrink: 0;
  }

  .toggle__btn {
    background: none;
    border: none;
    color: v.$text-muted;
    font-family: v.$font-mono;
    font-size: 0.7rem;
    padding: 3px 9px;
    cursor: pointer;
    &.active {
      background: v.$bg-raised;
      color: v.$accent;
    }
    &:hover:not(.active) {
      color: v.$text-primary;
    }
  }
</style>
