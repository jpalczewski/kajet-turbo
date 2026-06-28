<script lang="ts">
  import type { Snippet } from 'svelte';
  let { children }: { children: Snippet } = $props();
</script>

<p class="empty">{@render children()}</p>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  .empty {
    font-family: v.$font-mono;
    font-size: 0.85rem;
    color: v.$text-muted;
    margin: 0;
  }
</style>
