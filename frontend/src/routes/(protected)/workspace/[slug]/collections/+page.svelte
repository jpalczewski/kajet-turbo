<script lang="ts">
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
</script>

<div class="placeholder">
  <EmptyState>Wybierz kolekcję z listy.</EmptyState>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .placeholder {
    padding: v.$space-lg;
  }
</style>
