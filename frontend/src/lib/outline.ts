export type OutlineItem = { level: number; text: string; id: string };

const HEADING_RE = /<h([1-3])\b([^>]*)>([\s\S]*?)<\/h\1>/gi;

function stripTags(html: string): string {
  return html.replace(/<[^>]*>/g, '').trim();
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}

/**
 * Parses h1–h3 headings from server-rendered (markdown→bleach) HTML, slugifies
 * their text (unicode-aware, colliding slugs get `-2`, `-3`, …), and returns the
 * HTML with `id` attributes injected on those headings plus an outline using the
 * SAME ids — so anchor links from the outline resolve.
 */
export function processHeadings(html: string): { html: string; outline: OutlineItem[] } {
  const outline: OutlineItem[] = [];
  const seen = new Map<string, number>();

  const out = html.replace(HEADING_RE, (_match, lvl: string, attrs: string, inner: string) => {
    const level = Number(lvl);
    const text = stripTags(inner);
    const base = slugify(text) || 'section';
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    const id = count === 0 ? base : `${base}-${count + 1}`;
    outline.push({ level, text, id });
    const cleanedAttrs = attrs.replace(/\s+id="[^"]*"/i, '');
    return `<h${level}${cleanedAttrs} id="${id}">${inner}</h${level}>`;
  });

  return { html: out, outline };
}
