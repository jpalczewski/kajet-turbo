<script lang="ts">
  import { onMount } from 'svelte';
  import { invalidate } from '$app/navigation';
  import { wsConnection } from '$lib/ws/connection.svelte';
  import type { ServerEvent } from '$lib/ws/protocol';
  import { loadKeys } from '$lib/invalidation';

  let { children } = $props();

  onMount(() => {
    wsConnection.connect();
    return wsConnection.onEvent((event: ServerEvent) => {
      switch (event.type) {
        case 'note_updated':
          invalidate(loadKeys.note(event.note_id));
          invalidate(loadKeys.workspaceTree(event.workspace));
          break;
        case 'workspace_changed':
          invalidate(loadKeys.workspaceTree(event.workspace));
          break;
      }
    });
  });
</script>

{@render children()}
