<script lang="ts">
  import {
    apiRetryJobApiMeJobsJobIdRetryPost,
    apiDismissJobApiMeJobsJobIdDelete,
    getApiListJobsApiMeJobsGetUrl,
    type JobItem,
    type apiListJobsApiMeJobsGetResponse,
  } from '$lib/api';
  import { customFetch } from '$lib/api/fetcher';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';
  import { formatUnixDateTime } from '$lib/utils/format';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';

  let { data } = $props();

  // svelte-ignore state_referenced_locally
  let jobs = $state<JobItem[]>(data.jobs);
  let statusFilter = $state<string>('');
  const action = useAsyncAction();

  async function reload() {
    const url = statusFilter
      ? `${getApiListJobsApiMeJobsGetUrl()}?status=${encodeURIComponent(statusFilter)}`
      : getApiListJobsApiMeJobsGetUrl();
    const r = await customFetch<apiListJobsApiMeJobsGetResponse>(url);
    if (r.status === 200) jobs = r.data.jobs;
  }

  async function retry(id: string) {
    await action.run(async () => {
      const r = await apiRetryJobApiMeJobsJobIdRetryPost(id);
      if (r.status !== 200) {
        throw new Error('Nie udało się ponowić zadania.');
      }
      await reload();
    }, 'Nie udało się ponowić zadania.');
  }

  async function dismiss(id: string) {
    await action.run(async () => {
      const r = await apiDismissJobApiMeJobsJobIdDelete(id);
      if (r.status !== 200) {
        throw new Error('Nie udało się odrzucić zadania.');
      }
      await reload();
    }, 'Nie udało się odrzucić zadania.');
  }

  // Live status: poll while the page is mounted; re-run (and restart timer)
  // when the status filter changes.
  $effect(() => {
    void statusFilter; // re-run when the filter changes
    reload();
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  });

  const STATUS_LABELS: Record<string, string> = {
    pending: 'Oczekujące',
    running: 'W toku',
    done: 'Zakończone',
    failed: 'Błąd',
  };
</script>

<main class="page">
  <header class="page__header">
    <h1>Zadania</h1>
    <span class="page__count">{jobs.length}</span>
  </header>

  <div class="filters">
    <label class="filters__label">
      Status
      <select bind:value={statusFilter} class="filters__select">
        <option value="">Wszystkie</option>
        <option value="pending">Oczekujące</option>
        <option value="running">W toku</option>
        <option value="done">Zakończone</option>
        <option value="failed">Błąd</option>
      </select>
    </label>
  </div>

  {#if action.error}
    <p class="action-error">{action.error}</p>
  {/if}

  {#if jobs.length === 0}
    <EmptyState>Brak zadań.</EmptyState>
  {:else}
    <ul class="job-list">
      {#each jobs as job (job.id)}
        <li class="job-card">
          <div class="job-card__head">
            <span class="job-badge job-badge--{job.status}">
              {STATUS_LABELS[job.status] ?? job.status}
            </span>
            <span class="job-card__kind">
              {job.kind}{job.workspace ? `: ${job.workspace}` : ''}
            </span>
          </div>
          <dl class="job-card__meta">
            <div>
              <dt>Próby</dt>
              <dd>{job.attempts}/{job.max_attempts}</dd>
            </div>
            <div>
              <dt>Utworzono</dt>
              <dd>{formatUnixDateTime(job.created_at)}</dd>
            </div>
            <div>
              <dt>Zaktualizowano</dt>
              <dd>{formatUnixDateTime(job.updated_at)}</dd>
            </div>
          </dl>
          {#if job.last_error}
            <p class="job-card__error">{job.last_error}</p>
          {/if}
          <div class="job-card__actions">
            {#if job.status === 'failed'}
              <button
                type="button"
                class="btn-primary job-card__btn"
                disabled={action.busy}
                onclick={() => retry(job.id)}
              >
                Ponów
              </button>
            {/if}
            {#if job.status === 'done' || job.status === 'failed'}
              <button
                type="button"
                class="job-card__btn job-card__btn--danger"
                disabled={action.busy}
                onclick={() => dismiss(job.id)}
              >
                Odrzuć
              </button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 800px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .page__header {
    display: flex;
    align-items: baseline;
    gap: v.$space-md;
    margin-bottom: v.$space-xl;

    h1 {
      font-size: 1.5rem;
      font-family: v.$font-mono;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: v.$text-primary;
      margin: 0;
    }
  }

  .page__count {
    font-size: 0.75rem;
    font-family: v.$font-mono;
    color: v.$accent-dark;
    background: rgba(240, 184, 0, 0.08);
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    padding: 2px 8px;
  }

  .filters {
    margin-bottom: v.$space-xl;

    &__label {
      display: flex;
      flex-direction: column;
      gap: v.$space-xs;
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
      max-width: 200px;
    }

    &__select {
      padding: 7px 10px;
      background: v.$bg-surface;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      color: v.$text-primary;
      font-size: 0.85rem;
      font-family: v.$font-mono;

      &:focus {
        outline: none;
        border-color: v.$accent;
        box-shadow: 0 0 0 2px rgba(240, 184, 0, 0.12);
      }
    }
  }

  .action-error {
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$error;
    margin: 0 0 v.$space-md;
  }

  .job-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: v.$space-md;
  }

  .job-card {
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    padding: v.$space-md v.$space-lg;
    background: v.$bg-surface;

    &__head {
      display: flex;
      align-items: center;
      gap: v.$space-sm;
      margin-bottom: v.$space-sm;
    }

    &__kind {
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
    }

    &__meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: v.$space-xs v.$space-md;
      margin: 0 0 v.$space-sm;

      div {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }

      dt {
        font-size: 0.7rem;
        font-family: v.$font-mono;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: v.$text-muted;
      }

      dd {
        margin: 0;
        font-size: 0.82rem;
        font-family: v.$font-mono;
        color: v.$text-secondary;
      }
    }

    &__error {
      font-size: 0.78rem;
      font-family: v.$font-mono;
      color: v.$error;
      margin: 0 0 v.$space-sm;
      padding: v.$space-xs v.$space-sm;
      border: 1px solid rgba(255, 77, 77, 0.2);
      border-radius: v.$radius-sm;
      background: rgba(255, 77, 77, 0.04);
      word-break: break-word;
    }

    &__actions {
      display: flex;
      gap: v.$space-sm;
    }

    &__btn {
      width: auto;
      padding: 6px 14px;
      font-size: 0.85rem;

      &--danger {
        background: transparent;
        border: 1px solid v.$border;
        border-radius: v.$radius-md;
        color: v.$text-secondary;
        font-size: 0.85rem;
        font-family: v.$font-mono;
        cursor: pointer;
        transition:
          border-color 0.15s,
          color 0.15s;

        &:hover:not(:disabled) {
          border-color: v.$error;
          color: v.$error;
        }

        &:disabled {
          opacity: 0.5;
        }
      }
    }
  }

  .job-badge {
    font-size: 0.7rem;
    font-family: v.$font-mono;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-radius: v.$radius-sm;
    padding: 2px 7px;
    border: 1px solid;

    &--pending {
      color: v.$text-muted;
      border-color: v.$border;
      background: rgba(255, 255, 255, 0.03);
    }

    &--running {
      color: v.$accent;
      border-color: v.$accent;
      background: rgba(240, 184, 0, 0.06);
    }

    &--done {
      color: #4caf50;
      border-color: rgba(76, 175, 80, 0.4);
      background: rgba(76, 175, 80, 0.05);
    }

    &--failed {
      color: v.$error;
      border-color: rgba(255, 77, 77, 0.4);
      background: rgba(255, 77, 77, 0.05);
    }
  }
</style>
