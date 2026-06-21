<script lang="ts">
  import { page } from '$app/state';
  import { apiReindexWorkspaceApiWorkspacesNameReindexPost } from '$lib/api';
  import { apiErrorMessage } from '$lib/api/mutate';

  const slug = $derived(page.params.slug as string);

  let reindexing = $state(false);
  let reindexMsg = $state('');

  async function reindex() {
    reindexing = true;
    reindexMsg = '';
    try {
      const res = await apiReindexWorkspaceApiWorkspacesNameReindexPost(slug);
      reindexMsg =
        res.status === 200
          ? `Zreindeksowano ${res.data.count} notatek.`
          : 'Nie udało się zreindeksować.';
    } catch (e) {
      reindexMsg = apiErrorMessage(e, 'Nie udało się zreindeksować.');
    } finally {
      reindexing = false;
    }
  }
</script>

<main class="page">
  <h1>Ustawienia — {page.params.slug}</h1>
  <p class="hint">Ustawienia workspace'u — do implementacji.</p>

  <section class="reindex">
    <h2>Indeks wyszukiwania</h2>
    <p class="hint">Przebudowuje indeks wyszukiwania (chunki + wektory) z plików notatek.</p>
    <button type="button" class="btn-primary reindex__btn" disabled={reindexing} onclick={reindex}>
      {reindexing ? 'Reindeksowanie…' : 'Reindeksuj workspace'}
    </button>
    {#if reindexMsg}<p class="reindex__msg">{reindexMsg}</p>{/if}
  </section>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .reindex {
    margin-top: v.$space-lg;

    h2 {
      font-size: 1.1rem;
      margin-bottom: v.$space-sm;
    }

    &__btn {
      width: auto;
      padding: 9px 18px;
      white-space: nowrap;
    }

    &__msg {
      margin-top: v.$space-sm;
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }
  }
</style>
