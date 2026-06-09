<script lang="ts">
  import { page } from '$app/state'
  import { goto, invalidateAll } from '$app/navigation'
  import LoginForm from '$lib/components/LoginForm.svelte'

  const serverUrl = typeof window !== 'undefined' ? window.location.origin : ''

  async function handleLoginSuccess(_result: { email: string; redirect_uri?: string }) {
    await invalidateAll()
    await goto('/workspaces')
  }
</script>

<main>
  <p class="tagline">Twoje notatki markdown, dostępne w Claude.</p>

  <section>
    <h2>Połącz z Claude</h2>
    <p>Dodaj serwer MCP w Claude Desktop lub Claude Mobile:</p>
    <code class="code-block">{serverUrl}/mcp</code>
    <p class="hint">Claude zainicjuje logowanie przy pierwszym połączeniu.</p>
  </section>

  {#if !page.data.session}
    <section>
      <h2>Zaloguj się</h2>
      <LoginForm onSuccess={handleLoginSuccess} />
    </section>
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  main {
    max-width: 640px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  section { margin-bottom: v.$space-xl; }
</style>
