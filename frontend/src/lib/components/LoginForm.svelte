<script lang="ts">
  let { pendingId = '', submitLabel = 'Zaloguj się', onSuccess }: {
    pendingId?: string
    submitLabel?: string
    onSuccess: (data: { email: string; redirect_uri?: string }) => void
  } = $props()

  let email = $state('')
  let password = $state('')
  let error = $state('')
  let submitting = $state(false)

  async function handleSubmit(e: SubmitEvent) {
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
      onSuccess(data)
    } catch {
      error = 'Błąd sieci. Spróbuj ponownie.'
    } finally {
      submitting = false
    }
  }
</script>

{#if error}<p class="error">{error}</p>{/if}
<form onsubmit={handleSubmit}>
  <label>Email<input type="email" bind:value={email} required autocomplete="email" /></label>
  <label>Hasło<input type="password" bind:value={password} required autocomplete="current-password" /></label>
  <button type="submit" disabled={submitting} class="btn-primary">
    {submitting ? 'Logowanie…' : submitLabel}
  </button>
</form>
