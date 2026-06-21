# Frontend Mobile Overhaul — Design Spec

**Date:** 2026-06-21
**Status:** Approved

## Overview

Przebudowa frontendu tak, by był w pełni używalny na mobile (priorytet: iPhone/Safari), z **pełnym CRUD** na telefonie — nie tylko czytaniem. Trzy osie:

1. **Separacja logiki od UI** — czysta, testowalna logika w modułach `*.ts`; `.svelte` cienkie i deklaratywne.
2. **Testy jednostkowe** — `vitest` na wydzieloną logikę.
3. **Responsywność + reużywalne komponenty** — prymitywy UI deduplikujące kod, responsywny explorer w modelu drill-down.

Stan wyjściowy: SvelteKit 5 (runes), `adapter-static`, motyw black&gold, **zero media queries** w aplikacji. Rdzeń to sztywny 3-kolumnowy explorer (`grid-template-columns: 200px 280px 1fr; height: calc(100vh - 48px)`), który na ~390px daje poziomy scroll i chowa 2 z 3 paneli. Logika jest częściowo wydzielona (`tree.ts`, `tagTree.ts`, `tags.ts`, `format.ts`, `routes.ts`), brak testów frontendowych.

**Strategia: szybki działający mobile przed czystością warstw.** Zamiast najpierw budować cały fundament (testy + ekstrakcja), a dopiero potem UI, prowadzimy **interleave**: wchodzimy od razu w największy ból (responsywny explorer), a logikę wydzielamy *just-in-time* pod komponenty, których i tak dotykamy — z testami od razu przy ekstrakcji. Telefon staje się używalny po pierwszej-drugiej fazie, nie na końcu. Sekwencja w §6.

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
- `explorerView.ts` — `activePane(loadData) -> 'tree' | 'list' | 'preview'` na potrzeby drill-down na mobile. **Uwaga:** funkcja czyta wynik `load` (`noteId`, `folderPath`, `mode`), nie surowe `params` — bo to backend (`ls`) rozstrzyga, czy ostatni segment URL-a to folder czy notatka.

`validation.ts` **wycięte ze Spec** — dziś nie ma walidacji po stronie klienta (backend waliduje, front pokazuje błąd), więc to byłby nowy feature, nie ekstrakcja. Dodamy lekką walidację (np. pusty tytuł) ad hoc tylko jeśli realnie zaboli.

### 1b. `commands/` — cienkie akcje API

Opakowują wywołania orval + `apiErrorMessage` + `jsonBody`. Dziś logika ta jest zduplikowana inline w `notes/[...path]/+page.svelte` (`handleCreateFolder`, `handleCreateNote`, `handleMoveNote`) i w `MoveNoteDialog.svelte` (`moveNote`).

`$lib/commands/notes.ts`: `createNote`, `createFolder`, `moveNote` — zwracają znormalizowany wynik lub rzucają znormalizowany błąd. **Nawigacja (`goto`/`invalidate`) zostaje w komponencie** — to koncern UI, nie command. Commands są cienkie; testy opcjonalne (poza zakresem „logika"), wartość = dedup boilerplate'u (`jsonBody` + status-check + `apiErrorMessage`) + czyste komponenty.

**Minimalnie, bez ceremonii** — to garść funkcji, nie warstwa z abstrakcją. Jeśli w praktyce okaże się, że dają tylko narzut, zwijamy je z powrotem do kolokowanych helperów. Robimy je tylko przy komponentach, które i tak ruszamy (interleave).

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

Kluczowa właściwość: depth siedzi już w URL-u — `notes/<folder>` (ostatni segment = folder) vs `notes/<folder>/<noteId>` (ostatni segment = notatka), rozstrzygane w `load`. Więc to **kompozycja responsywna, nie nowy stan**. `explorerView.activePane(loadData)` wybiera widoczny panel; te same komponenty (`NotesList`, `NotePreview`) renderują się raz — desktop składa je obok siebie, mobile pokazuje aktywny. Breadcrumb/„wstecz" wynika z `breadcrumb.ts`.

### Centralny problem projektowy mobile: scalenie drzewo+lista

Na desktopie **drzewo folderów** (`FolderTree`, rekurencyjne, zawsze całe) i **lista notatek** (`NotesList`, płaska, bieżący folder) to dwa osobne panele obok siebie. Na mobile ekran pojedynczego folderu musi pokazać **i podfoldery, i notatki naraz** — inaczej nie da się zejść w głąb. To nie jest „pokaż 1 z 3 paneli", tylko **mobilny widok folderu** = (podfoldery jako wiersze nawigacyjne „w głąb") + (notatki jako wiersze otwierające podgląd). `activePane` rozróżnia tylko poziom (folder vs notatka); sam mobilny widok folderu to nowy, mały komponent kompozytowy nad istniejącymi danymi `tree`/`notes`. **To najtrudniejsza decyzja UX tej przebudowy** — rozstrzygamy ją w fazie explorera, na makietach w companionie.

Dialogi (`MoveNoteDialog`, `TagEditor`) przechodzą na `Modal`/Sheet → na mobile bottom-sheet.

---

## 5. Testy (`vitest`, logika)

- `vitest` w środowisku **node** (czyste funkcje, bez jsdom). `vitest.config.ts` (lub przez vite config). Skrypt `test` w `package.json`; dopięty do weryfikacji (skill `check` / `bun run test`).
- `*.test.ts` **kolokowane** przy modułach logiki (idiomatyczne dla vite).
- Cele: wszystkie moduły z §1a (`tree`, `tagTree`, `breadcrumb`, `noteLinks`, `tagEditor`, `explorerView`, `format`, builders w `routes`).
- Komponenty `.svelte` bez testów (zgodnie z decyzją). Jeśli w przyszłości komponenty — wtedy jsdom + Testing Library (poza zakresem).

**Trade-off:** kolokacja vs katalog `tests/`, node vs jsdom. Pure logic → node, szybko, bez DOM.

---

## 6. Sekwencja (interleave, ból-najpierw)

Jeden spec, fazy uporządkowane wg bólu mobile. Logika wydzielana *just-in-time* pod komponenty danej fazy, testy `vitest` od razu przy ekstrakcji. Każda faza niezależnie wdrażalna i daje widoczną poprawę na telefonie.

- **Faza 0 — Tani unblock (mała).** `vitest` + skrypt (włącza JIT-testy). `_breakpoints.scss` + `$bp-*`. Globalne mobile-fixy: `100dvh`, `viewport-fit=cover`, `env(safe-area-inset-*)`, hover-gating. Navbar responsywny (chrome dotyka każdego ekranu). Po tej fazie aplikacja przestaje być ucięta paskiem Safari.
- **Faza 1 — Responsywny explorer (największy ból).** `PaneStack` + drill-down; mobilny widok folderu scalający podfoldery+notatki (§4). JIT: `explorerView.ts` (+ testy), `breadcrumb.ts` (+ testy) pod „wstecz". `MoveNoteDialog` → `Modal`/Sheet (przenoszenie to core CRUD na mobile). Po tej fazie explorer jest używalny na iPhonie.
- **Faza 2 — Prymitywy przy okazji.** `Modal`, `Field`, `Button` (scalenie dwóch systemów), `IconButton`, `Chip`, `ListRow` — ekstrahowane wtedy, gdy dotykamy komponentu, który ich potrzebuje. `TagEditor` → `tagEditor.ts` (+ testy) + `Chip`, touch-friendly. `commands/notes.ts` przy refaktorze tworzenia/przenoszenia.
- **Faza 3 — Pozostałe route per-ekran.** Mobilne przejścia: `settings` (367 LOC), `note/[id]/edit`, `history`, `chunks`, `workspaces`. JIT: `noteLinks.ts` (+ testy) przy `NoteLinksPanel`.

Po akceptacji tego speca przechodzimy do `writing-plans` — plan rozpisze Fazy 0–1 szczegółowo (reszta jako kolejne kamienie milowe).

---

## Zakres wykluczony (YAGNI)

- Generyczny store/repository framework — tylko funkcje.
- Testy komponentów i e2e (Playwright) — osobny etap, jeśli kiedykolwiek.
- Container queries — tylko jeśli media queries nie wystarczą dla explorera.
- Łączenie/zmiana API backendu — przebudowa jest czysto frontendowa.
