<script lang="ts">
  import type { Snippet } from 'svelte';

  // The row's chrome only: the interactive element, its states and the touch target.
  // What goes inside (title, meta, icons) is the consumer's, since the rows differ in
  // both order and typography.
  let {
    href,
    onclick,
    active = false,
    variant = 'flush',
    layout = 'stack',
    children,
  }: {
    /** Renders an `<a>`; without it the row is a `<button>` driven by `onclick`. */
    href?: string;
    onclick?: (event: MouseEvent) => void;
    active?: boolean;
    /** `flush` rows sit edge to edge, divided by a rule; `card` rows are bordered boxes. */
    variant?: 'flush' | 'card';
    /** `stack` puts children in a column, `inline` in a row. */
    layout?: 'stack' | 'inline';
    children: Snippet;
  } = $props();

  const classes = $derived(
    `list-row list-row--${variant} list-row--${layout}${active ? ' list-row--active' : ''}`,
  );
</script>

{#if href === undefined}
  <button class={classes} type="button" aria-current={active ? 'true' : undefined} {onclick}>
    {@render children()}
  </button>
{:else}
  <!-- Callers pass hrefs built by the helpers in $lib/routes. -->
  <!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
  <a class={classes} {href} aria-current={active ? 'page' : undefined} {onclick}>
    {@render children()}
  </a>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .list-row {
    display: flex;
    width: 100%;
    min-height: 44px;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    text-decoration: none;
    cursor: pointer;

    &:focus-visible {
      outline: 2px solid v.$accent;
      outline-offset: -2px;
    }

    &--stack {
      flex-direction: column;
      justify-content: center;
      gap: 2px;
    }

    &--inline {
      align-items: center;
      gap: v.$space-sm;
    }

    &--flush {
      padding: v.$space-sm 12px;
      border: none;
      border-bottom: 1px solid v.$border;

      @include bp.hover {
        &:hover {
          background: rgba(255, 255, 255, 0.02);
        }
      }

      &.list-row--active {
        background: rgba(240, 184, 0, 0.06);
      }
    }

    &--card {
      padding: v.$space-sm v.$space-md;
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      transition:
        border-color 0.15s,
        background 0.15s;

      @include bp.hover {
        &:hover {
          border-color: v.$accent-dark;
        }
      }

      &.list-row--active {
        border-color: v.$accent;
        background: v.$bg-raised;
      }
    }
  }
</style>
