<script lang="ts">
  import { onMount } from 'svelte'

  const params = new URLSearchParams(window.location.search)
  const pendingId = params.get('pending')
  const isLogin = !!pendingId

  let serverUrl = window.location.origin
  let health = $state<'checking' | 'ok' | 'error'>('checking')

  // login state
  let email = $state('')
  let password = $state('')
  let loginError = $state('')
  let submitting = $state(false)

  onMount(async () => {
    if (isLogin) return
    try {
      const r = await fetch('/mcp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', method: 'ping', id: 1 }),
      })
      health = r.ok || r.status === 401 ? 'ok' : 'error'
    } catch {
      health = 'error'
    }
  })

  async function handleLogin(e: SubmitEvent) {
    e.preventDefault()
    submitting = true
    loginError = ''
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, pending_id: pendingId }),
      })
      const data = await res.json()
      if (!res.ok) {
        loginError = data.error ?? 'Błąd logowania.'
      } else {
        window.location.href = data.redirect_uri
      }
    } catch {
      loginError = 'Błąd sieci. Spróbuj ponownie.'
    } finally {
      submitting = false
    }
  }
</script>

{#if isLogin}
  <main class="login-page">
    <h1>kajet-turbo</h1>
    <p class="tagline">Zaloguj się, aby zezwolić na dostęp do notatek.</p>

    {#if loginError}
      <p class="error">{loginError}</p>
    {/if}

    <form onsubmit={handleLogin}>
      <label>
        Email
        <input type="email" bind:value={email} required autofocus autocomplete="email" />
      </label>
      <label>
        Hasło
        <input type="password" bind:value={password} required autocomplete="current-password" />
      </label>
      <button type="submit" disabled={submitting}>
        {submitting ? 'Logowanie…' : 'Zaloguj się i zezwól na dostęp'}
      </button>
    </form>
  </main>
{:else}
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
{/if}

<style>
  :global(body) { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; }

  main { max-width: 600px; margin: 0 auto; padding: 80px 24px; }
  h1 { font-size: 2.5rem; margin: 0 0 8px; }
  .tagline { color: #94a3b8; margin: 0 0 48px; font-size: 1.1rem; }

  /* landing */
  h2 { font-size: 1.1rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 12px; }
  section { margin-bottom: 40px; }
  code { display: block; background: #1e293b; padding: 12px 16px; border-radius: 8px; font-size: 0.95rem; margin: 12px 0; word-break: break-all; }
  .hint { color: #64748b; font-size: 0.875rem; }
  .status { display: flex; align-items: center; gap: 8px; font-size: 0.875rem; color: #64748b; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #334155; }
  .dot.ok { background: #22c55e; }
  .dot.error { background: #ef4444; }

  /* login form */
  .login-page { max-width: 400px; }
  form { display: flex; flex-direction: column; gap: 16px; }
  label { display: flex; flex-direction: column; gap: 6px; font-size: 0.875rem; color: #94a3b8; }
  input {
    padding: 10px 12px;
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #f8fafc;
    font-size: 1rem;
  }
  input:focus { outline: none; border-color: #60a5fa; }
  button {
    padding: 11px;
    background: #2563eb;
    color: #fff;
    border: none;
    border-radius: 6px;
    font-size: 1rem;
    cursor: pointer;
    transition: background 0.15s;
  }
  button:hover:not(:disabled) { background: #1d4ed8; }
  button:disabled { opacity: 0.6; cursor: not-allowed; }
  .error { color: #f87171; font-size: 0.875rem; margin: 0 0 8px; }
</style>
