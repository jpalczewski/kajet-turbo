<script lang="ts">
  let { pendingId, email, onLogout }: {
    pendingId: string
    email: string
    onLogout: () => void
  } = $props()

  let error = $state('')
  let submitting = $state(false)

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
</script>

<p class="hint">Zalogowany jako <strong>{email}</strong></p>
{#if error}<p class="error">{error}</p>{/if}
<button onclick={handleConsent} disabled={submitting} class="btn-primary">
  {submitting ? 'Autoryzowanie…' : 'Zezwól na dostęp'}
</button>
<button onclick={onLogout} class="btn-ghost">Wyloguj się</button>
