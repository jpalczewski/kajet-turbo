<!-- Test harness: the controller must be constructed during component init to own effects. -->
<script lang="ts">
  import RelatedNotesSection from '$lib/components/note/RelatedNotesSection.svelte';
  import { linkRelations } from '$lib/relatedNotes';
  import { RelatedNotesController } from '$lib/relatedNotes.svelte';
  import type { NoteLinkItem } from '$lib/api';

  let {
    slug,
    noteId,
    folder,
    backlinks = [],
    outlinks = [],
  }: {
    slug: string;
    noteId: string;
    folder: string;
    backlinks?: NoteLinkItem[];
    outlinks?: NoteLinkItem[];
  } = $props();

  const related = new RelatedNotesController(() => ({ slug, noteId, folder }));
</script>

<RelatedNotesSection
  {slug}
  phase={related.phase}
  status={related.status}
  items={related.items}
  scope={related.effectiveScope}
  canScopeToFolder={related.canScopeToFolder}
  relations={linkRelations(slug, backlinks, outlinks)}
  onscope={(scope) => related.setScope(scope)}
  onretry={() => related.retry()}
/>
