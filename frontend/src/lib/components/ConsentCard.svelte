<script lang="ts">
  import { apiConsentApiConsentPost } from '$lib/api'

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
      const result = await apiConsentApiConsentPost({
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pending_id: pendingId }),
      })
      if (result.status !== 200) { error = (result.data as any)?.error ?? 'Błąd.'; return }
      window.location.href = (result.data as any).redirect_uri
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
