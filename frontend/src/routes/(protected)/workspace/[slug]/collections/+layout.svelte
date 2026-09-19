<script lang="ts">
  import { page } from '$app/state';
  import { collectionPath } from '$lib/routes';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';

  let { data, children } = $props();
  let selected = $derived(page.params.collection);
</script>

<div class="collections" class:collections--detail={selected !== undefined}>
  <aside class="collections__list">
    <h1 class="collections__heading">Kolekcje</h1>
    {#if data.collections.length === 0}
      <div class="collections__empty">
        <EmptyState>
          Brak kolekcji. Definiuje się je narzędziem MCP <code>define_collection</code>.
        </EmptyState>
      </div>
    {:else}
      <ul>
        {#each data.collections as collection (collection.name)}
          <li>
            <a
              class="definition"
              class:definition--active={collection.name === selected}
              href={collectionPath(data.slug, collection.name)}
              aria-current={collection.name === selected ? 'page' : undefined}
            >
              <span class="definition__name">{collection.name}</span>
              <span class="definition__meta">{collection.grain} · {collection.cardinality}</span>
              {#if collection.description}
                <span class="definition__description">{collection.description}</span>
              {/if}
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </aside>

  <section class="collections__detail">
    {@render children()}
  </section>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .collections {
    display: grid;
    grid-template-columns: 280px 1fr;
    height: calc(100dvh - 48px);
    overflow: hidden;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    margin: v.$space-lg;
    background: v.$bg-surface;

    &__list {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: v.$bg-deep;
      border-right: 1px solid v.$border;

      ul {
        list-style: none;
        margin: 0;
        padding: 0;
        overflow-y: auto;
        flex: 1;
      }
    }

    &__heading {
      margin: 0;
      padding: 8px 12px;
      border-bottom: 1px solid v.$border;
      font-family: v.$font-mono;
      font-size: 0.72rem;
      font-weight: 400;
      color: v.$text-muted;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    &__empty {
      padding: v.$space-md;
    }

    &__detail {
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
  }

  .definition {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-height: 44px;
    padding: 8px 12px;
    border-bottom: 1px solid v.$border;
    text-decoration: none;

    &:hover {
      background: rgba(255, 255, 255, 0.02);
    }
    &--active {
      background: rgba(240, 184, 0, 0.06);
    }

    &__name {
      font-family: v.$font-mono;
      font-size: 0.85rem;
      color: v.$text-primary;
    }
    &__meta {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$accent-dark;
    }
    &__description {
      font-family: v.$font-sans;
      font-size: 0.75rem;
      color: v.$text-muted;
    }
  }

  // Mobile drill-down: depth lives in the URL. /collections shows the list, and
  // /collections/[name] shows only the detail pane with a back link to the list.
  @include bp.mobile {
    .collections {
      display: block;
      margin: 0;
      border: none;
      border-radius: 0;
      overflow-y: auto;
    }

    .collections__list,
    .collections__detail {
      height: 100%;
    }

    .collections__list {
      border-right: none;
    }

    .collections__detail {
      display: none;
    }
    .collections--detail .collections__list {
      display: none;
    }
    .collections--detail .collections__detail {
      display: flex;
    }
  }
</style>
