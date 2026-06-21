# Frontend Visual System — Design Spec (VHS/CRT)

**Date:** 2026-06-21
**Status:** Approved

## Overview

Odświeżenie estetyki frontendu w kierunku **subtelnego VHS/CRT** w palecie **złoto + fiolet + niebieski**. Złoto zostaje jako DNA marki; dochodzą fiolet i niebieski, czerń przesuwa się w stronę fioletu, a całość dostaje delikatną fakturę CRT (scanlines, lekka aberracja chromatyczna na nagłówkach, miękki glow na akcentach) — przy zachowaniu pełnej czytelności treści.

Temat jest przekrojowy, więc rozbity na trzy plany; ten spec to **Plan 1 — fundament systemu**:
- **Plan 1 (ten spec):** tokeny palety + typografia (JetBrains Mono, self-hosted) + reużywalne SCSS efektów VHS + aplikacja na globalny chrome (navbar, buttony, formularze, `Prose`, `Modal`/`IconButton`, reset/base).
- **Plan 2 (osobny):** aplikacja na kluczowe ekrany **+ responsywność tablet/desktop** (explorer, widok notatki, chipy, kodowanie kolorem tagów).
- **Plan 3 (osobny):** przemiał reszty ekranów (`settings`/`edit`/`history`/`chunks`/`workspaces`); zazębia się z funkcjonalną „Fazą 3".

Zasada nadrzędna: **fundament zmienia wartości tokenów i dokłada efekty, nie przepisuje układów.** Komponenty używające `v.$bg-*`/`v.$text-*`/`v.$accent` przebarwiają się automatycznie.

Stan wyjściowy: SCSS moduł-per-komponent, partiale w `frontend/src/lib/styles/` (`_variables`, `_breakpoints`, `_typography`, `_reset`, `_buttons`, `_forms`, `_utils`, `global.scss`), `adapter-static`. Obecna paleta jest złoto-centryczna na czerni; fonty: `$font-sans` (system-ui) + `$font-mono` (ui-monospace).

---

## 1. Paleta (migracja `_variables.scss`)

Czerń przesunięta w fiolet, tekst chłodny, złoto primary + fiolet/niebieski jako pełnoprawne akcenty. Nowe/zmienione wartości tokenów:

```scss
// tła (violet-tinted near-black)
$bg-deep:    #0b0912;
$bg-surface: #110d1c;
$bg-raised:  #1a1430;

// tekst (cool)
$text-primary:   #ece8f5;
$text-secondary: #9a8fc0;
$text-muted:     #5f5680;
$border:         #241d3a;
$border-accent:  #6a4fb0;

// akcenty
$accent:        #f0b800;  // gold (primary, brand) — zachowany
$accent-hover:  #ffc833;
$accent-dark:   #9a7800;
$violet:        #9d5cff;
$violet-bright: #b478ff;
$blue:          #3d8bff;
$blue-bright:   #6fa8ff;

$error: #ff4d6b;  // dostrojony do palety
```

Gradienty (`$gradient-accent`, `$gradient-warm`) przestrojone na złoto→fiolet (logo/akcenty) i fioletową czerń. Istniejące nazwy tokenów (`$accent`, `$bg-deep`, …) zostają — to minimalizuje rozjazd; nowe (`$violet*`, `$blue*`) dochodzą.

**Kodowanie kolorem tagów** (semantyka): rotacja `gold → violet → blue` po stabilnym hashu nazwy tagu (czysta funkcja `tagColor(tag)` w logice — testowalna; pełne wdrożenie w Planie 2, w fundamencie tylko tokeny).

---

## 2. Typografia (JetBrains Mono, self-hosted)

Mono jako backbone (pasuje do CRT/terminala). Podmieniamy generyczne `ui-monospace` na **JetBrains Mono** (wariant zmienny, self-hosted woff2) — charakterny, spójny na każdym OS.

- Plik: `frontend/static/fonts/JetBrainsMono[wght].woff2` (variable, wagi 100–800).
- **Subset musi obejmować `latin-ext`** (polskie znaki: ą ć ę ł ó ż ź ń ś) — krytyczne.
- `_fonts.scss` z `@font-face` (font-family `JetBrains Mono`, `font-display: swap`, `unicode-range` latin + latin-ext); `@use`-owany w `global.scss`.
- `app.html`: `<link rel="preload" as="font" type="font/woff2" crossorigin>` dla pliku.
- Tokeny: `$font-mono: 'JetBrains Mono', ui-monospace, monospace;`. `$font-sans` zostaje dla nielicznych miejsc UI, ale domyślny `font-family` body przechodzi na mono (terminal/CRT DNA).
- Nagłówki/logo: ten sam mono, traktowany wagą + `letter-spacing` + aberracją/glow (pkt 3). Bez osobnego display-fontu.

---

## 3. Efekty VHS (reużywalne SCSS — `_effects.scss`)

Nowy partial `_effects.scss` z trzema mixinami; subtelnie i **statycznie** (bez migotania → czytelność + perf):

```scss
@mixin scanlines { /* ::after overlay, repeating-linear-gradient, ~8% opacity, pointer-events:none */ }
@mixin aberration($offset: 1px) { /* text-shadow split: violet w lewo, blue w prawo */ }
@mixin glow($color, $strength: 0.5) { /* miękki box/text-shadow */ }
```

Zasady stosowania:
- `scanlines`: nakładane **raz** na app-shell (root layout / `.navbar`+main wrapper), nie per komponent.
- `aberration`: **wyłącznie** duże nagłówki i logo — nigdy treść notatki/UI (kontrast/czytelność).
- `glow`: stany aktywne i akcenty (aktywny folder/nota, primary button, focus) — oszczędnie.
- Treść notatki (`Prose`) zostaje **spokojna i jasna**, bez efektów.
- Brak animacji (statyczne), więc `prefers-reduced-motion` nie dotyczy; ewentualny przełącznik „reduce effects" to świadomy YAGNI (follow-up).

---

## 4. Aplikacja w fundamencie

Fundament aplikuje paletę + efekty na globalny chrome (bez zmian układu/logiki):
- **Reset/base** (`_reset.scss`): domyślny `background`/`color`/`font-family` (mono) z nowych tokenów.
- **Navbar**: tła/tekst z palety; logo = złoto→fiolet gradient + `aberration` + `glow`; link aktywny z `glow`. Scanlines na app-shell.
- **`_buttons.scss`**: primary = złoto (glow na hover), secondary/ghost = fiolet/obrys; spójność z nową paletą (oba systemy buttonów przebarwione, bez scalania — to issue #13).
- **`_forms.scss`**: focus border/box-shadow w złoto/fiolet.
- **`Prose.svelte`**: tekst `$text-primary` na `$bg-*`, linki w `$blue`, `code`/`pre` w `$bg-raised` z mono — bez efektów VHS.
- **`Modal`/`IconButton`**: tła/obrys z palety, `border-accent` fioletowy, ewentualny subtelny glow na headerze.

---

## 5. Testy i weryfikacja

Fundament jest czysto prezentacyjny (CSS/tokeny/font) → bez testów jednostkowych. Weryfikacja:
- `bun run check` (svelte-check) + `bun run lint` na **zmienionych** plikach (prettier/eslint).
- Istniejące **22 testy logiki muszą dalej przechodzić** (`bun run test`).
- Wizualnie: navbar, przykładowy widok notatki, dialog — desktop i mobile (efekty subtelne, treść czytelna, polskie znaki renderują się z webfontu).
- `tagColor(tag)` należy do Planu 2 (kodowanie tagów to praca na ekranach) — tu tylko tokeny `$violet*`/`$blue*`.

---

## 6. Zakres wykluczony (tu nie robimy)

- Układy tablet/desktop i restyl explorera/widoku notatki → **Plan 2**.
- Ekrany `settings`/`edit`/`history`/`chunks`/`workspaces` → **Plan 3**.
- Scalanie systemów buttonów / `Field`/`Chip`/`ListRow` → **issue #13**.
- Kodowanie kolorem tagów (`tagColor`) → **Plan 2**.
- Przełącznik „reduce effects", animowane migotanie CRT, motyw jasny → YAGNI.
