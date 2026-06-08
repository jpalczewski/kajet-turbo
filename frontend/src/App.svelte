<script lang="ts">
  import { onMount } from 'svelte'

  let serverUrl = window.location.origin
  let health = $state<'checking' | 'ok' | 'error'>('checking')

  onMount(async () => {
    try {
      const r = await fetch('/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', method: 'ping', id: 1 }) })
      health = r.ok || r.status === 401 ? 'ok' : 'error'
    } catch {
      health = 'error'
    }
  })
</script>

<main>
  <h1>kajet-turbo</h1>
  <p class="tagline">Twoje notatki markdown, dostępne w Claude.</p>

  <section class="connect">
    <h2>Połącz z Claude</h2>
    <p>Dodaj serwer MCP w Claude Desktop lub Claude Mobile:</p>
    <code>{serverUrl}/mcp</code>
    <p class="hint">Claude zainicjuje logowanie przy pierwszym połączeniu.</p>
  </section>

  <section class="status">
    <span class="dot" class:ok={health === 'ok'} class:error={health === 'error'}></span>
    {#if health === 'ok'}Serwer działa{:else if health === 'error'}Serwer niedostępny{:else}Sprawdzam…{/if}
  </section>
</main>

<style>
  :global(body) { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; }
  main { max-width: 600px; margin: 0 auto; padding: 80px 24px; }
  h1 { font-size: 2.5rem; margin: 0 0 8px; }
  .tagline { color: #94a3b8; margin: 0 0 48px; font-size: 1.1rem; }
  h2 { font-size: 1.1rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 12px; }
  section { margin-bottom: 40px; }
  code { display: block; background: #1e293b; padding: 12px 16px; border-radius: 8px; font-size: 0.95rem; margin: 12px 0; word-break: break-all; }
  .hint { color: #64748b; font-size: 0.875rem; }
  .status { display: flex; align-items: center; gap: 8px; font-size: 0.875rem; color: #64748b; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #334155; }
  .dot.ok { background: #22c55e; }
  .dot.error { background: #ef4444; }
</style>
