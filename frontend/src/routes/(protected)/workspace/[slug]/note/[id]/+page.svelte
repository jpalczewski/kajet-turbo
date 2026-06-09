<script lang="ts">
  import { page } from '$app/state'
</script>

<main class="page">
  <h1>Note #{page.params.id}</h1>
  <p class="hint">Szczegóły notatki — do implementacji.</p>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  .page { max-width: 800px; margin: 0 auto; padding: v.$space-2xl v.$space-lg; }
</style>
