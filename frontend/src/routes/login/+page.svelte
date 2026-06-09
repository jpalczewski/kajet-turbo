<script lang="ts">
  import { page } from '$app/state'
  import { invalidateAll } from '$app/navigation'
  import LoginForm from '$lib/components/LoginForm.svelte'
  import ConsentCard from '$lib/components/ConsentCard.svelte'

  let { data } = $props()

  async function handleLoginSuccess(result: { email: string; redirect_uri?: string }) {
    await invalidateAll()
    if (result.redirect_uri) window.location.href = result.redirect_uri
  }

  async function handleLogout() {
    await fetch('/api/session', { method: 'DELETE', credentials: 'include' })
    await invalidateAll()
  }
</script>

<main class="narrow">
  <h1>kajet-turbo</h1>
  <div class="card">
    <p class="label">Aplikacja prosi o dostęp do Twoich notatek:</p>
    <p class="client-name">{data.clientName}</p>
  </div>

  {#if page.data.session}
    <ConsentCard
      pendingId={data.pendingId}
      email={page.data.session.email}
      onLogout={handleLogout}
    />
  {:else}
    <p class="hint">Zaloguj się, aby zezwolić na dostęp.</p>
    <LoginForm
      pendingId={data.pendingId}
      submitLabel="Zaloguj się i zezwól na dostęp"
      onSuccess={handleLoginSuccess}
    />
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .narrow {
    max-width: 400px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
    display: flex;
    flex-direction: column;
    gap: v.$space-md;
  }

  h1 { font-size: 2rem; }
</style>
