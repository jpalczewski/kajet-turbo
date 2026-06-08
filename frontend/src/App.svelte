<script lang="ts">
  import { onMount } from 'svelte'

  const params = new URLSearchParams(window.location.search)
  const pendingId = params.get('pending') ?? ''
  const isLoginPath = window.location.pathname === '/login'

  type Session = { email: string }

  let session = $state<Session | null>(null)
  let loading = $state(true)
  let clientName = $state('Claude')

  let email = $state('')
  let password = $state('')
  let error = $state('')
  let submitting = $state(false)

  const serverUrl = window.location.origin

  onMount(async () => {
    if (isLoginPath && !pendingId) {
      window.location.replace('/')
      return
    }

    const [sessionRes, pendingRes] = await Promise.all([
      fetch('/api/session', { credentials: 'include' }).catch(() => null),
      pendingId
        ? fetch(`/api/pending?id=${pendingId}`).catch(() => null)
        : Promise.resolve(null),
    ])

    if (sessionRes?.ok) session = await sessionRes.json()
    if (pendingRes?.ok) {
      const d = await pendingRes.json()
      clientName = d.client_name ?? 'Claude'
    }

    loading = false
  })

  async function handleLogin(e: SubmitEvent) {
    e.preventDefault()
    submitting = true
    error = ''
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, pending_id: pendingId }),
      })
      const data = await res.json()
      if (!res.ok) { error = data.error ?? 'Błąd logowania.'; return }
      session = { email: data.email }
      if (data.redirect_uri) window.location.href = data.redirect_uri
      else window.location.replace('/')
    } catch {
      error = 'Błąd sieci. Spróbuj ponownie.'
    } finally {
      submitting = false
    }
  }

  async function handleConsent() {
    submitting = true
    error = ''
    try {
      const res = await fetch('/api/consent', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pending_id: pendingId }),
      })
      const data = await res.json()
      if (!res.ok) { error = data.error ?? 'Błąd.'; return }
      window.location.href = data.redirect_uri
    } catch {
      error = 'Błąd sieci.'
    } finally {
      submitting = false
    }
  }

  async function handleLogout() {
    await fetch('/api/session', { method: 'DELETE', credentials: 'include' })
    session = null
    email = ''
    password = ''
  }
</script>

{#if loading}
  <div class="center"><span class="spinner"></span></div>
{:else if isLoginPath && pendingId && session}
  <!-- Consent screen: already logged in -->
  <main class="narrow">
    <h1>kajet-turbo</h1>
    <div class="card">
      <p class="label">Aplikacja prosi o dostęp do Twoich notatek:</p>
      <p class="client-name">{clientName}</p>
    </div>
    <p class="hint">Zalogowany jako <strong>{session.email}</strong></p>
    {#if error}<p class="error">{error}</p>{/if}
    <button onclick={handleConsent} disabled={submitting} class="primary">
      {submitting ? 'Autoryzowanie…' : 'Zezwól na dostęp'}
    </button>
    <button onclick={handleLogout} class="ghost">Wyloguj się</button>
  </main>
{:else if isLoginPath && pendingId}
  <!-- Login + consent: not logged in -->
  <main class="narrow">
    <h1>kajet-turbo</h1>
    <div class="card">
      <p class="label">Aplikacja prosi o dostęp do Twoich notatek:</p>
      <p class="client-name">{clientName}</p>
    </div>
    <p class="hint">Zaloguj się, aby zezwolić na dostęp.</p>
    {#if error}<p class="error">{error}</p>{/if}
    <form onsubmit={handleLogin}>
      <label>Email<input type="email" bind:value={email} required autocomplete="email" /></label>
      <label>Hasło<input type="password" bind:value={password} required autocomplete="current-password" /></label>
      <button type="submit" disabled={submitting} class="primary">
        {submitting ? 'Logowanie…' : 'Zaloguj się i zezwól na dostęp'}
      </button>
    </form>
  </main>
{:else}
  <!-- Landing page -->
  <main>
    <header>
      <h1>kajet-turbo</h1>
      {#if session}
        <div class="session-bar">
          <span>{session.email}</span>
          <button onclick={handleLogout} class="ghost small">Wyloguj</button>
        </div>
      {/if}
    </header>

    <p class="tagline">Twoje notatki markdown, dostępne w Claude.</p>

    <section>
      <h2>Połącz z Claude</h2>
      <p>Dodaj serwer MCP w Claude Desktop lub Claude Mobile:</p>
      <code>{serverUrl}/mcp</code>
      <p class="hint">Claude zainicjuje logowanie przy pierwszym połączeniu.</p>
    </section>

    {#if !session}
      <section>
        <h2>Zaloguj się</h2>
        {#if error}<p class="error">{error}</p>{/if}
        <form onsubmit={handleLogin} class="inline-form">
          <label>Email<input type="email" bind:value={email} required autocomplete="email" /></label>
          <label>Hasło<input type="password" bind:value={password} required autocomplete="current-password" /></label>
          <button type="submit" disabled={submitting} class="primary">
            {submitting ? 'Logowanie…' : 'Zaloguj się'}
          </button>
        </form>
      </section>
    {/if}
  </main>
{/if}

<style>
  :global(body) { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; }
  :global(*) { box-sizing: border-box; }

  .center { display: flex; justify-content: center; align-items: center; height: 100vh; }
  .spinner { width: 24px; height: 24px; border: 3px solid #334155; border-top-color: #60a5fa; border-radius: 50%; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  main { max-width: 640px; margin: 0 auto; padding: 64px 24px; }
  main.narrow { max-width: 400px; }

  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 8px; flex-wrap: wrap; }
  h1 { font-size: 2rem; margin: 0; }
  h2 { font-size: 0.8rem; color: #64748b; text-transform: uppercase; letter-spacing: .08em; margin: 0 0 10px; }

  .tagline { color: #94a3b8; margin: 0 0 48px; font-size: 1.05rem; }
  section { margin-bottom: 40px; }

  code { display: block; background: #1e293b; padding: 12px 16px; border-radius: 8px; font-size: 0.9rem; margin: 10px 0; word-break: break-all; }
  .hint { color: #64748b; font-size: 0.875rem; margin: 6px 0 0; }

  .card { background: #1e293b; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px; }
  .label { color: #94a3b8; font-size: 0.875rem; margin: 0 0 6px; }
  .client-name { font-size: 1.1rem; font-weight: 600; margin: 0; }

  .session-bar { display: flex; align-items: center; gap: 10px; margin-left: auto; font-size: 0.875rem; color: #94a3b8; }

  form { display: flex; flex-direction: column; gap: 14px; }
  form.inline-form { max-width: 360px; }
  label { display: flex; flex-direction: column; gap: 5px; font-size: 0.875rem; color: #94a3b8; }
  input { padding: 9px 12px; background: #1e293b; border: 1px solid #334155; border-radius: 6px; color: #f8fafc; font-size: 0.95rem; }
  input:focus { outline: none; border-color: #60a5fa; }

  button.primary { padding: 10px 16px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-size: 0.95rem; cursor: pointer; transition: background .15s; }
  button.primary:hover:not(:disabled) { background: #1d4ed8; }
  button.primary:disabled { opacity: 0.55; cursor: not-allowed; }

  button.ghost { padding: 8px 14px; background: transparent; color: #64748b; border: 1px solid #334155; border-radius: 6px; font-size: 0.875rem; cursor: pointer; transition: border-color .15s, color .15s; }
  button.ghost:hover { border-color: #64748b; color: #94a3b8; }
  button.ghost.small { padding: 5px 10px; font-size: 0.8rem; }

  .error { color: #f87171; font-size: 0.875rem; margin: 0; }
</style>
