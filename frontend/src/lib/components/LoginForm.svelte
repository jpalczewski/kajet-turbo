<script lang="ts">
  import { apiLoginApiLoginPost } from '$lib/api'

  let { pendingId = '', submitLabel = 'Zaloguj się', onSuccess }: {
    pendingId?: string
    submitLabel?: string
    onSuccess: (data: { email: string; redirect_uri?: string | null }) => void
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
      const result = await apiLoginApiLoginPost({
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, pending_id: pendingId }),
      })
      onSuccess(result.data)
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
