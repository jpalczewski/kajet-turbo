<script lang="ts">
  import { onMount } from 'svelte';
  import { invalidate } from '$app/navigation';
  import { wsConnection } from '$lib/ws/connection.svelte';
  import { createInvalidationBatcher, eventKeys } from '$lib/ws/invalidationBatcher';

  let { children } = $props();

  onMount(() => {
    wsConnection.connect();
    const batcher = createInvalidationBatcher(invalidate);
    const unsubscribe = wsConnection.onEvent((event) => batcher.push(eventKeys(event)));
    return () => {
      unsubscribe();
      batcher.dispose();
    };
  });
</script>

{@render children()}
