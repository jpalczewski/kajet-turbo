<script lang="ts">
  import { apiConsentApiConsentPost } from '$lib/api';
  import { apiErrorMessage } from '$lib/api/mutate';

  let {
    pendingId,
    email,
    onLogout,
  }: {
    pendingId: string;
    email: string;
    onLogout: () => void;
  } = $props();

  let error = $state('');
  let submitting = $state(false);

  async function handleConsent() {
    submitting = true;
    error = '';
    try {
      const result = await apiConsentApiConsentPost({ pending_id: pendingId });
      if (result.status !== 200) throw new Error();
      window.location.href = result.data.redirect_uri;
    } catch (e) {
      error = apiErrorMessage(e, 'Błąd sieci.');
    } finally {
      submitting = false;
    }
  }
</script>

<p class="hint">Zalogowany jako <strong>{email}</strong></p>
{#if error}<p class="error">{error}</p>{/if}
<button onclick={handleConsent} disabled={submitting} class="btn-primary">
  {submitting ? 'Autoryzowanie…' : 'Zezwól na dostęp'}
</button>
<button onclick={onLogout} class="btn-ghost btn-ghost--block">Wyloguj się</button>
