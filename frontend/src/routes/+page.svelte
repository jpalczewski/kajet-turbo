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
  <h1 class="hero-title">kajet-turbo</h1>
  <p class="tagline">// twoje notatki markdown, dostępne w Claude.</p>

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
    max-width: 540px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .hero-title {
    font-size: 2.4rem;
    font-family: v.$font-mono;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    background: v.$gradient-accent;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: v.$space-sm;
  }

  section {
    margin-bottom: v.$space-xl;
    padding-top: v.$space-lg;
    border-top: 1px solid v.$border;

    &:first-of-type { border-top: none; }
  }

  p { color: v.$text-secondary; line-height: 1.6; }
</style>
