# Note View Dedup + Sidebar + Chunk Preview — Design Spec

**Date:** 2026-06-22
**Status:** Approved

## Overview

Notatka renderuje się dziś w trzech miejscach, każde z własnym kodem: podgląd w explorerze (`NotePreview` + `NoteLinksPanel`), pełny widok (`note/[id]/+page.svelte`), i osobna strona chunków (`note/[id]/chunks`). Skutki: backlinki zaimplementowane dwukrotnie (rail w preview vs sekcja `.backlinks` na dole pełnego widoku), tagi i akcje rozjeżdżają się między widokami, chunki to osobna wyspa.

Cel: **deduplikacja do współdzielonych klocków**, przy okazji realizująca dwie funkcje:
1. **Sidebar notatki** = Tagi · Outline · Backlinki · Wychodzące (jeden panel, prawy, zwijany).
2. **Podgląd chunków w explorerze** — toggle Treść↔Chunki w widoku notatki.

Zasada: jeden zestaw komponentów, z którego składają się i preview (gęsto), i pełny widok (luźno). Zero zmian backendu — wszystkie dane już istnieją (`note.tags`, `content_html`, `/links`, `/chunks`); outline liczony po stronie klienta.

Stan wyjściowy (dane): `note` = `{ note_id, title, folder, tags: string[], content_html, updated_at }`; `/links` → `{ backlinks, outlinks }` (`NoteLinkItem = { note_id, folder, title }`); `/chunks` → `{ title, index_state, chunk_count, chunks: [{ ordinal, header_path, char_count, embedded, content }] }`.

---

## 1. Współdzielone komponenty (`$lib/components/note/`)

- **`NoteMeta.svelte`** — sidebar po prawej, zwijany (zachowuje wzorzec rail/localStorage z obecnego `NoteLinksPanel`). Sekcje, każda chowana gdy pusta:
  - **Tagi** — chipy (link do `tagsPath`).
  - **Outline** — lista `{level, text, id}` h1–h3; klik = scroll do `#id` w treści.
  - **Backlinki** — `NoteLinkItem[]` (link `noteInTreePath`).
  - **Wychodzące** — `NoteLinkItem[]`.
  Props: `slug`, `tags: string[]`, `outline: OutlineItem[]`, `backlinks`, `outlinks`, `showOutline: boolean`. **Outline pokazywany tylko w trybie Treść** — `showOutline` steruje parent.
- **`NoteBody.svelte`** — renderuje treść wg propa `mode: 'content' | 'chunks'`. Tryb Treść: `<Prose html={html} />`. Tryb Chunki: `<ChunkList ... />` z **lazy-load** (fetch `/chunks` przy pierwszym wejściu w tryb Chunki, cache w komponencie; spinner w trakcie). Props: `slug`, `noteId`, `html` (już z kotwicami), `mode`. **Stan trybu jest własnością parenta** (segmentowany toggle Treść↔Chunki renderowany w nagłówku kompozycji), bo `NoteMeta.showOutline` od niego zależy — parent trzyma `mode` i przekazuje do `NoteBody` (`mode`) i `NoteMeta` (`showOutline = mode === 'content'`).
- **`ChunkList.svelte`** — lista chunków wyjęta 1:1 z obecnej strony `/chunks` (breadcrumb `header_path`, meta `#ordinal · char_count · embedded`, `<pre>` content; badge `index_state`/`chunk_count` jako opcjonalny nagłówek przez prop). Reużywana przez `NoteBody` (toggle) i stronę `/chunks`.
- **`NoteActions.svelte`** — akcje jako linki/dialog: `Edit`, `History`, `Chunki`(→ pełna strona, opcjonalny), `Move` (`MoveNoteDialog`), `↗` (pełny widok). Props sterują które pokazać (`variant: 'preview' | 'full'`). Zastępuje rozjazd akcji w `NotePreview.__actions` i headerze pełnego widoku.

---

## 2. Outline — czysta logika (`$lib/outline.ts`)

```ts
export type OutlineItem = { level: number; text: string; id: string };
// Parsuje nagłówki h1–h3 z server-renderowanego HTML, slugifikuje tekst (z deduplikacją
// kolizji: 'plan', 'plan-2', ...), zwraca HTML z dodanymi id ORAZ outline z tymi samymi id.
export function processHeadings(html: string): { html: string; outline: OutlineItem[] };
```

- Regex po `<h[1-3]>…</h[1-3]>` (HTML jest przewidywalny — generowany z markdown serwerowo, sanitizowany bleachem). Slug = lowercase, spacje→`-`, usunięcie nie-alnum (unicode-aware, polskie znaki zachowane), dedup sufiksem `-N`. Te same id w HTML i w outline → kotwice działają.
- Wywoływane **raz w parencie** kompozycji: `const { html, outline } = processHeadings(note.content_html)`; `html` → `Prose`, `outline` → `NoteMeta`. `Prose` bez zmian logiki (dalej `{@html}` + delegacja kliknięć wikilinków).
- Czyste, testowalne w env node (string→string), bez DOM.

---

## 3. Kompozycja

- **`NotePreview.svelte`** (explorer, prawy panel): header(`path` + `NoteActions variant="preview"` + toggle) → `NoteBody` (lewa) + `NoteMeta` (prawa). Składane z klocków.
- **`note/[id]/+page.svelte`** (pełny): `Breadcrumb` + header(title + `NoteActions variant="full"`) + ten sam `NoteBody` + `NoteMeta`. Usuwa inline-tagi z headera (→ `NoteMeta`) i dolną sekcję `.backlinks` (→ `NoteMeta`). Load dorzuca `outlinks` (dziś zwraca tylko backlinks) — drobna zmiana `+page.ts` (już woła `/links`).
- **`note/[id]/chunks/+page.svelte`** → cienki wrapper: `back-link` + `<ChunkList preview={data.preview} showHeader />`. Logika/markup listy znika z tej strony do `ChunkList`.

---

## 4. Testy i weryfikacja

- `vitest`: `outline.ts` — `processHeadings`: brak nagłówków, h1/h2/h3, deduplikacja kolizji slugów, polskie znaki/diakrytyki, nagłówki z zagnieżdżonym inline (`<code>`/`<em>`) → czysty tekst, id wstrzyknięte i zgodne z outline.
- Komponenty (`NoteMeta`/`NoteBody`/`ChunkList`/`NoteActions`) — bez testów jednostkowych (prezentacja).
- Po każdym kroku: `bun run check` + `bun run test` (istniejące 22 testy + nowe outline zielone); `bunx prettier --write` na zmienionych plikach.
- Wizualnie: preview i pełny widok mają ten sam sidebar; toggle Treść↔Chunki ładuje chunki; outline scrolluje; `/chunks` działa jak dziś.

---

## 5. Zakres / kolejność (jeden plan)

1. **`ChunkList.svelte`** — wydzielić z `/chunks`; strona → wrapper. (Dedup #1, bez zmian wizualnych.)
2. **`outline.ts`** (TDD) + wpięcie `processHeadings` w kompozycję (na razie tylko id w `Prose`).
3. **`NoteActions.svelte`** — wydzielić akcje; wpiąć w preview + pełny widok.
4. **`NoteMeta.svelte`** — 4 sekcje (prawy, zwijany); zastępuje `NoteLinksPanel` + dolne backlinki + inline-tagi; load pełnego widoku dorzuca `outlinks`.
5. **`NoteBody.svelte`** — `Prose` + toggle + lazy `ChunkList`.
6. **Złożenie** `NotePreview` i `note/[id]` na klockach; usunięcie martwego kodu (`NoteLinksPanel`, stare sekcje).

Każdy krok zostawia działającą aplikację.

---

## 6. Zakres wykluczony (YAGNI)

- Zmiany backendu (outline/anchor po stronie serwera, nowe endpointy) — wszystko z istniejących danych.
- Wyszukiwarka semantyczna / podgląd embeddingów w sidebarze — osobny temat.
- Pełna responsywność tablet/desktop tego układu → należy do Visual Plan 2 (osobny). Tu trzymamy się obecnych breakpointów (sidebar zwijany na wąskich).
- `Field`/`Button`/`Chip`/`ListRow` dedup → issue #13.
