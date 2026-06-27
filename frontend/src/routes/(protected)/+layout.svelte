<script lang="ts">
  import { onMount } from 'svelte';
  import { invalidate } from '$app/navigation';
  import { wsConnection } from '$lib/ws/connection.svelte';
  import type { ServerEvent } from '$lib/ws/protocol';

  let { children } = $props();

  onMount(() => {
    wsConnection.connect();
    return wsConnection.onEvent((event: ServerEvent) => {
      if (event.type === 'note_updated') {
        invalidate(`app:note:${event.note_id}`);
      }
    });
  });
</script>

{@render children()}
