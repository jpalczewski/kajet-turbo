<script lang="ts">
  import { groupEntries } from '$lib/collectionGroups';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
  import ListRow from '$lib/components/ui/ListRow.svelte';
  import { collectionsPath, notePath } from '$lib/routes';
  import { DEFAULT_DATE_PREFS } from '$lib/utils/format';

  let { data } = $props();
  const datePrefs = $derived(data.session?.preferences ?? DEFAULT_DATE_PREFS);
  const groups = $derived(groupEntries(data.definition.grain, data.notes, datePrefs));
  // With cardinality "many" several entries share a period, so the title (with its
  // ordinal) is what tells the rows apart; for "one" it just repeats the period.
  const showTitle = $derived(data.definition.cardinality === 'many');
</script>

<a class="back" href={collectionsPath(data.slug)}>‹ Wstecz</a>

<header class="header">
  <h1 class="header__name">{data.definition.name}</h1>
  <span class="header__count">{data.notes.length}</span>
  {#if data.definition.description}
    <p class="header__description">{data.definition.description}</p>
  {/if}
</header>

<div class="entries">
  {#if groups.length === 0}
    <div class="entries__empty">
      <EmptyState>Brak wpisów.</EmptyState>
    </div>
  {:else}
    {#each groups as group (group.key)}
      <section class="group">
        {#if group.label}
          <h2 class="group__label">{group.label}</h2>
        {/if}
        <ul>
          {#each group.rows as row (row.note.note_id)}
            <li>
              <ListRow href={notePath(data.slug, row.note.note_id)} layout="inline">
                <span class="entry-period">{row.label}</span>
                {#if showTitle && row.label !== row.note.title}
                  <span class="entry-title">{row.note.title}</span>
                {/if}
              </ListRow>
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .back {
    display: none;
    flex-shrink: 0;
    padding: 10px 12px;
    border-bottom: 1px solid v.$border;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    color: v.$accent-dark;
    text-decoration: none;

    @include bp.mobile {
      display: block;
    }
  }

  .header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: v.$space-sm;
    padding: 8px 12px;
    border-bottom: 1px solid v.$border;
    flex-shrink: 0;

    &__name {
      margin: 0;
      font-family: v.$font-mono;
      font-size: 0.85rem;
      font-weight: 400;
      color: v.$text-primary;
    }
    &__count {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$accent-dark;
      background: rgba(240, 184, 0, 0.08);
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 6px;
    }
    &__description {
      flex-basis: 100%;
      margin: 0;
      font-size: 0.75rem;
      color: v.$text-muted;
    }
  }

  .entries {
    flex: 1;
    overflow-y: auto;

    &__empty {
      padding: v.$space-lg;
    }
  }

  .group {
    &__label {
      position: sticky;
      top: 0;
      margin: 0;
      padding: 6px 12px;
      background: v.$bg-deep;
      border-bottom: 1px solid v.$border;
      font-family: v.$font-mono;
      font-size: 0.72rem;
      font-weight: 400;
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    ul {
      list-style: none;
      margin: 0;
      padding: 0;
    }
  }

  .entry-period {
    font-family: v.$font-mono;
    font-size: 0.85rem;
    color: v.$text-primary;
  }
  .entry-title {
    font-family: v.$font-mono;
    font-size: 0.75rem;
    color: v.$text-muted;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
