<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    label,
    onclick,
    children,
  }: {
    label: string;
    onclick: (event: MouseEvent) => void;
    children: Snippet;
  } = $props();
</script>

<button class="icon-button" type="button" aria-label={label} {onclick}>
  {@render children()}
</button>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .icon-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: none;
    background: none;
    color: v.$text-muted;
    font-size: 1.25rem;
    line-height: 1;
    cursor: pointer;

    @include bp.hover {
      &:hover {
        color: v.$text-primary;
      }
    }

    @include bp.mobile {
      width: 44px;
      height: 44px;
    }
  }
</style>
