<script lang="ts">
  import type { RelatedNoteItem, RelatedNotesStatus } from '$lib/api';
  import { headingLabel, type LinkRelation, type RelatedScope } from '$lib/relatedNotes';
  import { noteInTreePath } from '$lib/routes';

  let {
    slug,
    phase,
    status,
    items,
    scope,
    canScopeToFolder,
    relations,
    onscope,
    onretry,
  }: {
    slug: string;
    phase: 'loading' | 'error' | 'ready';
    status: RelatedNotesStatus | null;
    items: RelatedNoteItem[];
    scope: RelatedScope;
    canScopeToFolder: boolean;
    relations: Map<string, LinkRelation>;
    onscope: (scope: RelatedScope) => void;
    onretry: () => void;
  } = $props();

  const MESSAGES = {
    loading: 'Szukam powiązanych notatek…',
    pending: 'Notatka jest jeszcze indeksowana — powiązania pojawią się po zakończeniu.',
    unavailable: 'Wyszukiwanie semantyczne nie jest skonfigurowane.',
    empty: 'Notatka nie ma jeszcze treści do porównania.',
    none: 'Brak powiązanych notatek w tym zakresie.',
  } as const;

  const RELATION_LABELS: Record<LinkRelation, string> = {
    backlink: 'Ta notatka linkuje do bieżącej',
    outlink: 'Bieżąca notatka linkuje do tej',
    both: 'Notatki linkują do siebie nawzajem',
  };

  // `ready` with no items is its own case: ranking ran and found nothing.
  const message = $derived.by(() => {
    if (phase === 'loading') return MESSAGES.loading;
    if (phase === 'error') return null;
    if (status === 'ready') return items.length === 0 ? MESSAGES.none : null;
    return status ? MESSAGES[status] : null;
  });
</script>

<section class="related" aria-labelledby="related-heading" aria-busy={phase === 'loading'}>
  <div class="related__head">
    <h4 class="related__heading" id="related-heading">Powiązane</h4>
    {#if canScopeToFolder}
      <div class="related__scope" role="group" aria-label="Zakres wyszukiwania powiązanych">
        <button
          class:related__scope--active={scope === 'workspace'}
          aria-pressed={scope === 'workspace'}
          onclick={() => onscope('workspace')}>Workspace</button
        >
        <button
          class:related__scope--active={scope === 'folder'}
          aria-pressed={scope === 'folder'}
          onclick={() => onscope('folder')}>Folder</button
        >
      </div>
    {/if}
  </div>

  {#if phase === 'error'}
    <div class="related__status related__status--error" role="alert">
      <p>Nie udało się pobrać powiązanych notatek.</p>
      <button class="related__retry" onclick={onretry}>Spróbuj ponownie</button>
    </div>
  {:else if message}
    <p class="related__status" role="status">{message}</p>
  {:else if phase === 'ready'}
    <ul class="related__list">
      {#each items as item (item.note_id)}
        {@const relation = relations.get(item.note_id)}
        {@const heading = headingLabel(item.target_header_path)}
        <li>
          <a class="related__link" href={noteInTreePath(slug, item.folder, item.note_id)}>
            <span class="related__title">
              {item.title}
              {#if relation}
                <span class="related__linked" title={RELATION_LABELS[relation]} aria-hidden="true"
                  >↔</span
                >
                <span class="related__sr">({RELATION_LABELS[relation]})</span>
              {/if}
            </span>
            {#if item.folder || heading}
              <span class="related__where">
                {#if item.folder}<span class="related__folder">{item.folder}/</span>{/if}
                {#if heading}<span class="related__fragment">{heading}</span>{/if}
              </span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .related {
    padding: v.$space-md 12px;
    border-top: 1px solid v.$border;

    &__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: v.$space-sm;
      margin-bottom: v.$space-sm;
    }

    &__heading {
      margin: 0;
      font-family: v.$font-mono;
      font-size: 0.68rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: v.$text-secondary;
    }

    &__scope {
      display: flex;
      gap: 2px;

      button {
        border: 1px solid v.$border;
        background: v.$bg-surface;
        color: v.$text-muted;
        cursor: pointer;
        font-family: v.$font-mono;
        font-size: 0.65rem;
        padding: 3px 5px;

        &:hover,
        &.related__scope--active {
          border-color: v.$border-accent;
          color: v.$text-primary;
        }
      }
    }

    &__status {
      margin: 0;
      color: v.$text-muted;
      font-family: v.$font-mono;
      font-size: 0.75rem;

      &--error {
        color: v.$error;

        p {
          margin: 0 0 v.$space-xs 0;
        }
      }
    }

    &__retry {
      border: 1px solid v.$border;
      background: v.$bg-surface;
      color: v.$text-secondary;
      cursor: pointer;
      font: inherit;
      padding: 3px 6px;

      &:hover {
        border-color: v.$border-accent;
        color: v.$text-primary;
      }
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: v.$space-sm;
    }

    &__link {
      display: flex;
      flex-direction: column;
      gap: 1px;
      min-width: 0;
      text-decoration: none;
      color: v.$blue;
      font-family: v.$font-mono;
      font-size: 0.78rem;
      word-break: break-word;

      &:hover {
        color: v.$blue-bright;

        .related__title {
          text-decoration: underline;
        }
      }
    }

    &__linked {
      margin-left: 3px;
      color: v.$accent-dark;
    }

    &__where {
      display: flex;
      flex-wrap: wrap;
      gap: 0 v.$space-xs;
      font-size: 0.68rem;
      color: v.$text-muted;
    }

    &__fragment::before {
      content: '§ ';
    }

    &__sr {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
    }
  }
</style>
