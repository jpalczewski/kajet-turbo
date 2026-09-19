// Test stand-in for `$app/paths`: substitutes route params the way SvelteKit does, so
// path helpers in `$lib/routes` produce realistic URLs without the SvelteKit runtime.
export const base = '';

export function resolve(id: string, params: Record<string, string> = {}): string {
  const path = id
    .replace(/\/\([^)]+\)/g, '')
    .replace(/\[\.\.\.(\w+)\]/g, (_, key: string) => params[key] ?? '')
    .replace(/\[(\w+)\]/g, (_, key: string) => encodeURIComponent(params[key] ?? ''));
  return path.replace(/\/+$/, '') || '/';
}
