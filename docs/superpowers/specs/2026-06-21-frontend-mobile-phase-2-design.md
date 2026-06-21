# Frontend Mobile — Phase 2 Design Spec

**Date:** 2026-06-21
**Status:** Approved

## Overview

Druga faza przebudowy frontendu pod mobile. Faza 0–1 (zmergowana do `main`, merge `34218f5`) dała responsywny navbar, globalne mobile-fixy i drill-down explorer w trybie Pliki. Faza 2 domyka **funkcjonalność mobilną**, której jeszcze brakuje:

1. Dialogi (`MoveNoteDialog`) używalne na telefonie — bottom-sheet zamiast desktopowego dialogu.
2. **Wybór tagu na mobile** — dziś w trybie tagi nie da się wybrać tagu (sidebar z drzewem ukryty na telefonie).
3. `TagEditor` dotykowy + wydzielona, testowana logika.

Strategia (potwierdzona): **mobile-funkcjonalne najpierw**. Budujemy tylko prymitywy, które realnie odblokowują telefon teraz; czysty dedup (Button/Field/Chip/ListRow) jest świadomie odłożony — śledzony w **GitHub issue #13**, nie w tym specu.

Nadrzędny spec: `2026-06-21-frontend-mobile-overhaul-design.md`. Konwencje: SvelteKit 5 runes, SCSS moduł-per-komponent, `vitest` (env node), bun; kod/komentarze po angielsku.

---

## 1. Prymityw `Modal` / Sheet (+ `IconButton`)

Wyciągnięty z chrome `<dialog>` w `MoveNoteDialog.svelte` (header z tytułem + przycisk zamknięcia, zamykanie kliknięciem w backdrop, slot na treść, slot na akcje).

- **Desktop:** wyśrodkowany dialog jak dziś (`width: min(420px, calc(100vw - 32px))`, border, radius).
- **Mobile (`@include bp.mobile`):** **bottom-sheet** — przyklejony do dołu, pełna szerokość, zaokrąglona góra, dekoracyjny drag-handle u góry, padding z `env(safe-area-inset-bottom)`.
- **Zamknięcie:** tap w backdrop + przycisk `×`. **Bez swipe-to-dismiss** (obsługa gestów jest kruchá i kosztowna — YAGNI).

Bazujemy na natywnym `<dialog>` (focus-trap, `::backdrop`, `showModal()` zostają) — zmieniamy wyłącznie prezentację na mobile, nie budujemy modala od zera.

`IconButton.svelte` — mały, ikonowy przycisk z **44×44px hit-area** wokół ikony (np. `×`). Wychodzi naturalnie z `Modal` (przycisk zamknięcia); pierwszy konsument to header `Modal`.

**Interfejs `Modal`** (zdecydowane): komponent zarządza własnym `<dialog>` i **eksportuje funkcje `show()` / `close()`** (Svelte 5 `export function`), konsument trzyma referencję przez `bind:this` i woła `modal.show()` — mirror obecnego `MoveNoteDialog` (`dialog.showModal()`/`dialog.close()`). Propsy: `title: string`. Sloty: domyślny (treść) + `actions` (przyciski stopki). Opcjonalny callback `onclose`. Komponent nie zna logiki domeny — `MoveNoteDialog` przekazuje treść i akcje.

**Konsument w tej fazie:** refactor `MoveNoteDialog` na `Modal` + `IconButton` (przenoszenie notatki = core CRUD na mobile). Zachowanie i wygląd na desktopie bez zmian.

**Trade-off:** trzymamy `<dialog>` zamiast custom-modala (mniej kodu, natywna dostępność); cena — bottom-sheet to stylowanie elementu `dialog`, nie osobny komponent „sheet". Akceptowalne.

---

## 2. Wybór tagu inline na mobile (wariant A)

W `MobileFolderNav.svelte`, w gałęzi `mode === 'tags'`, renderujemy istniejący `<TagTree>` na górze panelu listy — analogicznie do listy podfolderów w trybie Pliki — wraz z toggle „z podtagami" (`includeDescendants`).

- Reużywa `TagTree` as-is (props: `tags`, `currentTag`, `includeDescendants`, `slug`) — **zero nowej logiki nawigacji**.
- Spójne z drill-downem trybu Pliki: użytkownik ląduje w trybie tagi, widzi drzewo tagów na górze, tapie tag → notatki tagu pojawiają się poniżej (istniejący `NotesList`).
- Dane (`data.tags`, `data.tagPath`, `data.includeDescendants`) są już w `load` explorera — trzeba je tylko przekazać do `MobileFolderNav` jako propsy.
- `TagTree` ukryty na desktopie wraz z całym `MobileFolderNav` (`display:none` poza `@include bp.mobile`); desktopowy sidebar z `TagTree` bez zmian.

Domyka to udokumentowaną lukę Fazy 1 (brak wyboru tagu na mobile).

---

## 3. `TagEditor` dotykowy + `tagEditor.ts`

**Wydzielenie logiki (TDD).** Z `TagEditor.svelte` przenosimy derywacje `$derived.by` do `frontend/src/lib/tagEditor.ts` jako czyste funkcje:
- `computeCandidates(tags: string[], suggestions: string[]): string[]` — znormalizowane (`normalizeTag`), zdeduplikowane sugestie, z pominięciem już zastosowanych tagów.
- `computeOptions(query: string, candidates: string[], tags: string[]): Option[]` — dopasowania po query (≤8) plus opcja „create" gdy query daje świeży, poprawny tag niebędący dokładnym istniejącym dopasowaniem. `type Option = { value: string; isCreate: boolean }`.

`tagEditor.ts` importuje tylko `normalizeTag` z `$lib/tags` (czysty, testowalny w env node). `TagEditor.svelte` woła te funkcje w `$derived`; reszta (stan `query`/`activeIndex`/`focused`, obsługa klawiatury, ARIA) zostaje w komponencie.

**Dotykowość.** Chipy, input i wiersze dropdownu dostają cele ≥44px; style `:hover` w `TagEditor` zamykamy w `@media (hover: hover)` (mixin `bp.hover`), by na dotyku hover się nie zacinał. Konsument: edycja notatki na telefonie.

---

## 4. Zakres wykluczony (odłożone — issue #13)

`Button` (ujednolicenie dwóch systemów CSS), `Field` (label+input+error), `Chip`, `ListRow` — czysty dedup, nie zmienia używalności mobile. Odłożone do dedykowanego dedup-passa, śledzone w GitHub **#13**. Również: swipe-to-dismiss dla sheeta; pełna piramida testów (komponenty/e2e).

---

## 5. Testy

`vitest` (env node, kolokowany `frontend/src/lib/tagEditor.test.ts`):
- `computeCandidates`: normalizacja, dedup, pominięcie zastosowanych tagów.
- `computeOptions`: filtrowanie po query, limit 8, obecność/nieobecność opcji „create" (świeży tag vs dokładne istniejące dopasowanie vs już zastosowany).

`Modal`/`TagTree`-inline/`IconButton` bez testów jednostkowych (prezentacja; zgodnie z decyzją „tylko logika"). Po każdym kroku: `bun run check` + `bun run test`.

---

## 6. Sekwencja

1. **`Modal` + `IconButton`**, refactor `MoveNoteDialog` na nie (desktop bez zmian, mobile = bottom-sheet).
2. **Tag-tree inline** w `MobileFolderNav` (tryb tagi) + toggle „z podtagami"; przekazanie `tags`/`tagPath`/`includeDescendants` z explorera.
3. **`tagEditor.ts`** (TDD) → `TagEditor` na nim + dotykowość (44px, hover-gating).

Każdy krok niezależnie wdrażalny i daje widoczny efekt na telefonie. Po akceptacji speca → `writing-plans`.
