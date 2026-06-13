<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';
  import { normalizeTag } from '$lib/tags';

  let {
    tags = $bindable<string[]>([]),
    suggestions = [],
  }: {
    tags: string[];
    suggestions?: string[];
  } = $props();

  let query = $state('');
  let activeIndex = $state(-1);
  let focused = $state(false);

  type Option = { value: string; isCreate: boolean };

  // Normalized, deduped suggestion paths that aren't already applied.
  let candidates = $derived.by(() => {
    const seen = new SvelteSet<string>(tags);
    const out: string[] = [];
    for (const raw of suggestions) {
      const n = normalizeTag(raw);
      if (n && !seen.has(n)) {
        seen.add(n);
        out.push(n);
      }
    }
    return out;
  });

  let options = $derived.by<Option[]>(() => {
    const normalizedQuery = normalizeTag(query);
    const needle = normalizedQuery ?? '';
    const matches = needle ? candidates.filter((c) => c.includes(needle)).slice(0, 8) : [];
    const opts: Option[] = matches.map((value) => ({ value, isCreate: false }));
    // Offer a "create" option when the query yields a fresh, valid path that
    // isn't already applied and isn't an exact existing suggestion match.
    if (normalizedQuery && !tags.includes(normalizedQuery) && !matches.includes(normalizedQuery)) {
      opts.push({ value: normalizedQuery, isCreate: true });
    }
    return opts;
  });

  let showDropdown = $derived(focused && query.trim().length > 0 && options.length > 0);

  function addTag(raw: string) {
    const n = normalizeTag(raw);
    if (n && !tags.includes(n)) tags = [...tags, n];
    query = '';
    activeIndex = -1;
  }

  function removeTag(tag: string) {
    tags = tags.filter((t) => t !== tag);
  }

  function selectOption(opt: Option) {
    addTag(opt.value);
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') {
      if (options.length === 0) return;
      event.preventDefault();
      activeIndex = activeIndex < options.length - 1 ? activeIndex + 1 : 0;
    } else if (event.key === 'ArrowUp') {
      if (options.length === 0) return;
      event.preventDefault();
      activeIndex = activeIndex > 0 ? activeIndex - 1 : options.length - 1;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (activeIndex >= 0 && activeIndex < options.length) {
        selectOption(options[activeIndex]);
      } else {
        addTag(query);
      }
    } else if (event.key === 'Escape') {
      query = '';
      activeIndex = -1;
      focused = false;
    }
  }
</script>

<div class="tag-editor">
  {#if tags.length}
    <ul class="chips">
      {#each tags as tag (tag)}
        <li class="chip">
          <span class="chip-label">#{tag}</span>
          <button
            type="button"
            class="chip-remove"
            aria-label={`Usuń tag #${tag}`}
            onclick={() => removeTag(tag)}>✕</button
          >
        </li>
      {/each}
    </ul>
  {/if}

  <div class="input-wrap">
    <input
      class="tag-input"
      type="text"
      bind:value={query}
      placeholder="Dodaj tag…"
      autocomplete="off"
      role="combobox"
      aria-expanded={showDropdown}
      aria-controls="tag-editor-listbox"
      aria-autocomplete="list"
      onkeydown={onKeydown}
      onfocus={() => (focused = true)}
      onblur={() => (focused = false)}
    />

    {#if showDropdown}
      <ul id="tag-editor-listbox" class="dropdown" role="listbox">
        {#each options as opt, i (opt.isCreate ? `__create__${opt.value}` : opt.value)}
          <li>
            <button
              type="button"
              class="option"
              class:active={i === activeIndex}
              role="option"
              aria-selected={i === activeIndex}
              onmouseenter={() => (activeIndex = i)}
              onmousedown={(event) => {
                // Select on mousedown (before blur) so the click isn't eaten by
                // the input losing focus first.
                event.preventDefault();
                selectOption(opt);
              }}
            >
              {#if opt.isCreate}
                Utwórz „#{opt.value}"
              {:else}
                #{opt.value}
              {/if}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .tag-editor {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;
    font-family: v.$font-mono;
  }

  .chips {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: v.$space-xs;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: v.$space-xs;
    padding: 1px v.$space-xs 1px v.$space-sm;
    font-size: 0.78rem;
    color: v.$accent-dark;
    background: v.$bg-raised;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
  }

  .chip-label {
    white-space: nowrap;
  }

  .chip-remove {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    width: 14px;
    height: 14px;
    font-size: 0.7rem;
    line-height: 1;
    background: none;
    border: none;
    color: v.$text-muted;
    cursor: pointer;
    &:hover {
      color: v.$accent;
    }
  }

  .input-wrap {
    position: relative;
  }

  .tag-input {
    width: 100%;
    box-sizing: border-box;
    padding: v.$space-xs v.$space-sm;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    color: v.$text-primary;
    background: v.$bg-raised;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
    &::placeholder {
      color: v.$text-muted;
    }
    &:focus {
      outline: none;
      border-color: v.$accent-dark;
    }
  }

  .dropdown {
    position: absolute;
    top: calc(100% + 2px);
    left: 0;
    right: 0;
    z-index: 20;
    list-style: none;
    margin: 0;
    padding: v.$space-xs 0;
    max-height: 240px;
    overflow-y: auto;
    background: v.$bg-surface;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
  }

  .option {
    display: block;
    width: 100%;
    padding: v.$space-xs v.$space-sm;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    text-align: left;
    background: none;
    border: none;
    color: v.$text-muted;
    cursor: pointer;
    &:hover,
    &.active {
      background: v.$bg-raised;
      color: v.$accent;
    }
  }
</style>
