<script lang="ts">
  import { onMount } from 'svelte';
  import { invalidate } from '$app/navigation';
  import { wsConnection } from '$lib/ws/connection.svelte';
  import type { ServerEvent } from '$lib/ws/protocol';

  let { children } = $props();

  onMount(() => {
    wsConnection.connect();
    return wsConnection.onEvent((event: ServerEvent) => {
      switch (event.type) {
        case 'note_updated':
          invalidate(`app:note:${event.note_id}`);
          invalidate('app:workspace-tree');
          break;
        case 'workspace_changed':
          invalidate('app:workspace-tree');
          break;
      }
    });
  });
</script>

{@render children()}
