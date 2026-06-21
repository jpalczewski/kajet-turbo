# Frontend Mobile Overhaul — Design Spec

**Date:** 2026-06-21
**Status:** Approved

## Overview

Przebudowa frontendu tak, by był w pełni używalny na mobile (priorytet: iPhone/Safari), z **pełnym CRUD** na telefonie — nie tylko czytaniem. Trzy osie, prowadzone w kolejności „fundament najpierw":

1. **Separacja logiki od UI** — czysta, testowalna logika w modułach `*.ts`; `.svelte` cienkie i deklaratywne.
2. **Testy jednostkowe** — `vitest` na wydzieloną logikę.
3. **Responsywność + reużywalne komponenty** — prymitywy UI deduplikujące kod, responsywny explorer w modelu drill-down.

Stan wyjściowy: SvelteKit 5 (runes), `adapter-static`, motyw black&gold, **zero media queries** w aplikacji. Rdzeń to sztywny 3-kolumnowy explorer (`grid-template-columns: 200px 280px 1fr; height: calc(100vh - 48px)`), który na ~390px daje poziomy scroll i chowa 2 z 3 paneli. Logika jest częściowo wydzielona (`tree.ts`, `tagTree.ts`, `tags.ts`, `format.ts`, `routes.ts`), brak testów frontendowych.

Projekt dzielimy na **dwa specy/plany**: **Spec A (fundament)** i **Spec B (mobile UI)**. Ten dokument opisuje wspólną architekturę i zakres obu; do implementacji wchodzi najpierw Spec A.

---

## 1. Separacja logiki od UI

Konwencja: każda funkcja = czysta logika `*.ts` + cienki `.svelte`. Trzy rodzaje modułów.

### 1a. Pure transforms (cel testów `vitest`)

Brak importów Svelte, brak `fetch` — czyste funkcje, trywialnie testowalne.

Istniejące (objąć testami): `tree.ts`, `tagTree.ts`, `tags.ts` (`normalizeTag`), `format.ts`, `routes.ts` (buildery ścieżek).

Nowe — wydzielone z komponentów:
- `breadcrumb.ts` — segmenty breadcrumb ze ścieżki (logika z `Breadcrumb.svelte`).
- `noteLinks.ts` — parsowanie/grupowanie linków (logika z `NoteLinksPanel.svelte`).
- `tagEditor.ts` — derywacje z `TagEditor.svelte`: `computeCandidates(tags, suggestions)` i `computeOptions(query, candidates, tags)` (dziś jako `$derived.by` inline). To realna logika warta testów (normalizacja, dedup, opcja „create").
- `validation.ts` — reguły walidacji: tytuł notatki, ścieżka folderu, nazwa workspace.
- `explorerView.ts` — `activePane(params) -> 'tree' | 'list' | 'preview'` na potrzeby drill-down na mobile.

### 1b. `commands/` — cienkie akcje API

Opakowują wywołania orval + `apiErrorMessage` + `jsonBody`. Dziś logika ta jest zduplikowana inline w `notes/[...path]/+page.svelte` (`handleCreateFolder`, `handleCreateNote`, `handleMoveNote`) i w `MoveNoteDialog.svelte` (`moveNote`).

`$lib/commands/notes.ts`: `createNote`, `createFolder`, `moveNote` — zwracają znormalizowany wynik lub rzucają znormalizowany błąd. **Nawigacja (`goto`/`invalidate`) zostaje w komponencie** — to koncern UI, nie command. Commands są cienkie; testy opcjonalne (poza zakresem „logika"), wartość = dedup + czyste komponenty.

### 1c. Prezentacja (`.svelte`)

Props in, callbacks out, `$derived` tylko do glue. Bez `fetch`, bez rozgałęzień biznesowych. `load` zostaje w `+page.ts` (już rozdzielone).

**Trade-off:** więcej plików + jedna warstwa pośrednia, w zamian testowalność i dedup. Świadomie **nie** budujemy generycznego store/repository frameworka (YAGNI) — same funkcje, kolokowane przy funkcji (zgodnie z obecną konwencją), współdzielone w `$lib/`.

---

## 2. System responsywny

- `_breakpoints.scss` z mixinami: `mobile` (`max-width: $bp-mobile`), `tablet-down`, `hover` (`@media (hover: hover)`), `touch`. `$bp-mobile: 768px`, `$bp-tablet: 1024px` w `_variables.scss` — jedno źródło prawdy.
- Globalne fixy mobile:
  - `100vh` → `100dvh` (pasek URL Safari ucina treść).
  - `viewport-fit=cover` w `app.html` + `env(safe-area-inset-*)` w navbarze (sticky top) i ewentualnych paskach dolnych (notch/home indicator).
  - Minimalny tap target 44×44px na interaktywnych prymitywach.
  - Style `:hover` wyłącznie za `@media (hover: hover)` — żeby na dotyku hover się nie „zacinał".

**Trade-off:** mixiny SCSS (pasują do obecnego modelu „SCSS-moduł per komponent") zamiast utility-class czy container queries. Container queries rozważymy tylko dla paneli explorera, jeśli zajdzie potrzeba — domyślnie media queries.

---

## 3. Prymitywy UI (dedup)

Ekstrakcja tylko tam, gdzie ≥2 realne użycia lub wyraźna potrzeba mobile. Katalog w `$lib/components/ui/`:

| Prymityw | Zastępuje / konsumenci | Mobile |
| --- | --- | --- |
| `Modal.svelte` (Sheet) | chrome `<dialog>` z `MoveNoteDialog` (header+close, backdrop-click-close, slot akcji) | na mobile bottom-sheet (slide-up, full-width, safe-area) |
| `Field.svelte` | label+input/select+error z `MoveNoteDialog`, `settings`, formularzy | pełna szerokość, 44px |
| `Button.svelte` | **ujednolica** dwa systemy CSS (`.btn-primary/.btn-ghost` + `.btn--*`) w jeden komponent z `variant` | większy tap target |
| `IconButton.svelte` | close `×`, chip-remove `✕`, „Przenieś" trigger | 44px hit area wokół małej ikony |
| `Chip.svelte` | tag chip z `TagEditor`, read-only tagi gdzie indziej | — |
| `ListRow.svelte` | wiersze `NotesList`, `FolderTree`, `VersionList` | wysokość dotykowa |
| `PaneStack.svelte` | kontener explorera | drill-down (patrz §4) |

**Decyzja (zatwierdzona):** dwa świadomie rozdzielone systemy buttonów (komentarz „do not merge" w `_buttons.scss`) **scalamy** w jeden `Button.svelte` z wariantami `{primary, ghost, secondary, danger}`. Markup zdeduplikowany, rozróżnienie wizualne zachowane przez style wariantów. Alternatywa (zostawić dwa CSS-y) odrzucona — dedup jest celem wprost.

---

## 4. Responsywny explorer (drill-down stack)

Wybrany model nawigacji mobilnej: **drill-down** (wariant A). Na desktopie ≥768px bez zmian — grid 3-kolumnowy (drzewo | lista | podgląd). Na mobile `PaneStack` pokazuje **jeden panel na ekran**, pełna szerokość; „w głąb" (folder → lista → notatka) i „wstecz" w górę hierarchii.

Kluczowa właściwość: nawigacja siedzi już w URL-ach (`notes/[...path]`, `note/[id]`), więc to **kompozycja responsywna, nie nowy stan**. `explorerView.activePane(params)` wybiera widoczny panel; te same komponenty (`FolderTree`/`TagTree`, `NotesList`, `NotePreview`) renderują się raz — desktop składa je obok siebie, mobile pokazuje aktywny. Breadcrumb/„wstecz" wynika z `breadcrumb.ts`.

Dialogi (`MoveNoteDialog`, `TagEditor`) przechodzą na `Modal`/Sheet → na mobile bottom-sheet.

---

## 5. Testy (`vitest`, logika)

- `vitest` w środowisku **node** (czyste funkcje, bez jsdom). `vitest.config.ts` (lub przez vite config). Skrypt `test` w `package.json`; dopięty do weryfikacji (skill `check` / `bun run test`).
- `*.test.ts` **kolokowane** przy modułach logiki (idiomatyczne dla vite).
- Cele: wszystkie moduły z §1a (`tree`, `tagTree`, `breadcrumb`, `noteLinks`, `tagEditor`, `validation`, `explorerView`, `format`, builders w `routes`).
- Komponenty `.svelte` bez testów (zgodnie z decyzją). Jeśli w przyszłości komponenty — wtedy jsdom + Testing Library (poza zakresem).

**Trade-off:** kolokacja vs katalog `tests/`, node vs jsdom. Pure logic → node, szybko, bez DOM.

---

## 6. Dekompozycja i sekwencja

### Spec A — Fundament (wchodzi pierwszy do planu)
1. **Tooling:** `vitest` + skrypt; `_breakpoints.scss` + `$bp-*`; globalne mobile-fixy (`100dvh`, `viewport-fit`, safe-area, hover-gating). Bez zmian wizualnych poza fixami.
2. **Wydzielenie logiki:** przeniesienie czystych funkcji z komponentów do `*.ts` (§1a) + `commands/` (§1b); komponenty bez zmian behawioralnych.
3. **Testy:** pokrycie `vitest` modułów z §1a.

### Spec B — Mobile UI (osobny brainstorm/plan później)
4. **Prymitywy** (§3): `Modal/Sheet`, `Field`, `Button`, `IconButton`, `Chip`, `ListRow`; refactor istniejących komponentów na nie.
5. **Responsywny explorer** (§4): `PaneStack` drill-down; navbar mobile; dialogi → sheety.
6. **Przejścia per-route:** `settings` (367 LOC), `note/[id]/edit`, `history`, `chunks`, `workspaces`.

Każda faza niezależnie wdrażalna. Po akceptacji tego speca przechodzimy do `writing-plans` dla **Spec A**.

---

## Zakres wykluczony (YAGNI)

- Generyczny store/repository framework — tylko funkcje.
- Testy komponentów i e2e (Playwright) — osobny etap, jeśli kiedykolwiek.
- Container queries — tylko jeśli media queries nie wystarczą dla explorera.
- Łączenie/zmiana API backendu — przebudowa jest czysto frontendowa.
