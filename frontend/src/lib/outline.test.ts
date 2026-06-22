import { describe, expect, it } from 'vitest';
import { processHeadings } from './outline';

describe('processHeadings', () => {
  it('returns empty for content without headings', () => {
    const r = processHeadings('<p>hello</p>');
    expect(r.outline).toEqual([]);
    expect(r.html).toBe('<p>hello</p>');
  });

  it('extracts h1–h3 with slug ids and injects matching ids', () => {
    const r = processHeadings('<h1>Plan Q3</h1><h2>Priorytety</h2><h3>Ryzyka</h3>');
    expect(r.outline).toEqual([
      { level: 1, text: 'Plan Q3', id: 'plan-q3' },
      { level: 2, text: 'Priorytety', id: 'priorytety' },
      { level: 3, text: 'Ryzyka', id: 'ryzyka' },
    ]);
    expect(r.html).toBe(
      '<h1 id="plan-q3">Plan Q3</h1><h2 id="priorytety">Priorytety</h2><h3 id="ryzyka">Ryzyka</h3>',
    );
  });

  it('deduplicates colliding slugs', () => {
    const r = processHeadings('<h2>Plan</h2><h2>Plan</h2><h2>Plan</h2>');
    expect(r.outline.map((o) => o.id)).toEqual(['plan', 'plan-2', 'plan-3']);
  });

  it('keeps Polish diacritics in slugs', () => {
    const r = processHeadings('<h2>Zażółć gęślą</h2>');
    expect(r.outline[0]).toEqual({ level: 2, text: 'Zażółć gęślą', id: 'zażółć-gęślą' });
  });

  it('strips nested inline markup from heading text and slug', () => {
    const r = processHeadings('<h3>Use <code>code</code> here</h3>');
    expect(r.outline[0]).toEqual({ level: 3, text: 'Use code here', id: 'use-code-here' });
  });

  it('ignores h4 and deeper', () => {
    const r = processHeadings('<h4>Deep</h4>');
    expect(r.outline).toEqual([]);
    expect(r.html).toBe('<h4>Deep</h4>');
  });
});
