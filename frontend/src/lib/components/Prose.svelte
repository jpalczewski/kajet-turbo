<script lang="ts">
  import { goto } from '$app/navigation';
  import { notePath } from '$lib/routes';

  let { html }: { html: string } = $props();

  // Wikilinks are injected via {@html} as <a class="wikilink" href="/workspace/{slug}/note/{id}">.
  // Such anchors bypass SvelteKit's link interception, so a plain click would full-page reload.
  // Delegate clicks here and route client-side, rebuilding the path through notePath() to satisfy
  // svelte/no-navigation-without-resolve. Modifier/middle clicks fall through to the browser.
  const WIKILINK_HREF = /^\/workspace\/([^/]+)\/note\/([^/]+)$/;

  function handleClick(event: MouseEvent) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    const anchor = (event.target as HTMLElement).closest('a.wikilink');
    const href = anchor?.getAttribute('href');
    const match = href?.match(WIKILINK_HREF);
    if (!match) return;
    event.preventDefault();
    goto(notePath(match[1], match[2]));
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="prose" onclick={handleClick}>
  <!-- eslint-disable-next-line svelte/no-at-html-tags -- content_html is sanitized server-side with bleach -->
  {@html html}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  // .prose is a real Svelte element (has scoped hash).
  // :global(el) inside it compiles to .prose[hash] el — matches injected HTML children.
  .prose {
    color: v.$text-primary;
    font-size: 0.95rem;
    line-height: 1.7;

    :global(h1),
    :global(h2),
    :global(h3),
    :global(h4) {
      font-family: v.$font-mono;
      color: v.$text-primary;
      margin: v.$space-xl 0 v.$space-md 0;
      line-height: 1.3;
    }

    :global(h1) {
      font-size: 1.5rem;
    }
    :global(h2) {
      font-size: 1.25rem;
    }
    :global(h3) {
      font-size: 1.05rem;
    }

    :global(p) {
      margin: 0 0 v.$space-md 0;
    }

    :global(a) {
      color: v.$accent;
      text-decoration: underline;
      text-underline-offset: 3px;
    }

    :global(a:hover) {
      color: v.$accent-hover;
    }

    // Wikilinks: distinct from external links (dotted underline, cursor pointer).
    :global(a.wikilink) {
      text-decoration-style: dotted;
      cursor: pointer;
    }

    // Broken wikilink: target no longer resolves — flag it, not clickable.
    :global(.wikilink-broken) {
      color: v.$text-muted;
      text-decoration: line-through;
      text-decoration-color: v.$accent-dark;
      cursor: help;
    }

    :global(ul),
    :global(ol) {
      padding-left: v.$space-lg;
      margin: 0 0 v.$space-md 0;
    }

    :global(li) {
      margin-bottom: v.$space-xs;
    }

    :global(code) {
      font-family: v.$font-mono;
      font-size: 0.85em;
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 5px;
      color: v.$accent-light;
    }

    :global(pre) {
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      padding: v.$space-md;
      overflow-x: auto;
      margin: 0 0 v.$space-md 0;
    }

    :global(pre code) {
      background: none;
      border: none;
      padding: 0;
      color: v.$text-primary;
      font-size: 0.85rem;
    }

    :global(blockquote) {
      margin: 0 0 v.$space-md 0;
      padding: v.$space-sm v.$space-md;
      border-left: 3px solid v.$accent-dark;
      background: v.$bg-raised;
      color: v.$text-secondary;
      font-style: italic;
    }

    :global(blockquote p) {
      margin: 0;
    }

    :global(hr) {
      border: none;
      border-top: 1px solid v.$border;
      margin: v.$space-xl 0;
    }

    :global(table) {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: v.$space-md;
      font-family: v.$font-mono;
      font-size: 0.85rem;
    }

    :global(th),
    :global(td) {
      padding: v.$space-sm v.$space-md;
      border: 1px solid v.$border;
      text-align: left;
    }

    :global(th) {
      background: v.$bg-raised;
      color: v.$text-secondary;
    }
  }
</style>
