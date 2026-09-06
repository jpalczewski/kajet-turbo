<script lang="ts">
  import { apiLoginApiLoginPost } from '$lib/api';
  import { apiErrorMessage } from '$lib/api/mutate';

  let {
    pendingId = '',
    submitLabel = 'Zaloguj się',
    onSuccess,
  }: {
    pendingId?: string;
    submitLabel?: string;
    onSuccess: (data: { email: string; redirect_uri?: string | null }) => void;
  } = $props();

  let email = $state('');
  let password = $state('');
  let error = $state('');
  let submitting = $state(false);

  async function handleSubmit(e: SubmitEvent) {
    e.preventDefault();
    submitting = true;
    error = '';
    try {
      const result = await apiLoginApiLoginPost({ email, password, pending_id: pendingId });
      // customFetch (api/fetcher.ts) throws on any non-2xx response before returning here,
      // so this branch is unreachable at runtime -- it exists to narrow the generated
      // client's ErrorResponse | LoginResponse union for TypeScript. Route it through
      // apiErrorMessage (not a bare `throw new Error()`) so it'd surface the translated
      // AuthError code if that invariant ever stops holding.
      if (result.status !== 200) throw new Error(apiErrorMessage(result, 'Błąd logowania.'));
      onSuccess(result.data);
    } catch (e) {
      error = apiErrorMessage(e, 'Błąd sieci. Spróbuj ponownie.');
    } finally {
      submitting = false;
    }
  }
</script>

{#if error}<p class="error">{error}</p>{/if}
<form onsubmit={handleSubmit}>
  <label>Email<input type="email" bind:value={email} required autocomplete="email" /></label>
  <label
    >Hasło<input
      type="password"
      bind:value={password}
      required
      autocomplete="current-password"
    /></label
  >
  <button type="submit" disabled={submitting} class="btn-primary">
    {submitting ? 'Logowanie…' : submitLabel}
  </button>
</form>
