<script lang="ts">
  let { html }: { html: string } = $props();
</script>

<div class="prose">
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
