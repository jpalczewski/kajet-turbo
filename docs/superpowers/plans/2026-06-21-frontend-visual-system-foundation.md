# Frontend Visual System — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Założyć wizualny fundament VHS/CRT (paleta złoto+fiolet+niebieski, JetBrains Mono, reużywalne efekty scanlines/aberracja/glow) i nałożyć go na globalny chrome — bez przepisywania układów ekranów.

**Architecture:** Migracja wartości tokenów w `_variables.scss` (komponenty używające `v.$bg-*`/`v.$text-*`/`v.$accent` przebarwiają się automatycznie) + nowy partial `_effects.scss` z mixinami + samohostowany font przez fontsource. Aplikacja efektów: scanlines raz na `body`, aberracja/glow tylko na nagłówkach/logo/akcentach; treść notatki zostaje spokojna.

**Tech Stack:** SvelteKit 5, SCSS (moduł-per-komponent), `@fontsource-variable/jetbrains-mono`, bun.

**Scope:** Plan 1 (fundament) ze specu `docs/superpowers/specs/2026-06-21-frontend-visual-system-design.md`. Ekrany+tablet → Plan 2; reszta ekranów → Plan 3; `tagColor` → Plan 2; dedup → issue #13.

**Konwencje:** kod po angielsku; **przed każdym commitem** uruchom `bunx prettier --write` na zmienionych plikach (repo ma pre-existing drift w innych plikach — NIE formatuj cudzych); po każdym tasku `bun run check` + `bun run test` (22 testy logiki muszą zostać zielone).

---

## File Structure

Nowe:
- `frontend/src/lib/styles/_effects.scss` — mixiny `scanlines`, `aberration`, `glow`, `text-glow`.

Modyfikowane:
- `frontend/package.json` / `bun.lock` — dep `@fontsource-variable/jetbrains-mono`.
- `frontend/src/routes/+layout.svelte` — import fontu.
- `frontend/src/lib/styles/_variables.scss` — `$font-mono` + migracja palety + `$violet*`/`$blue*` + gradienty.
- `frontend/src/lib/styles/_reset.scss` — body font mono + scanlines.
- `frontend/src/lib/styles/_typography.scss` — subtelna aberracja na `h1`.
- `frontend/src/lib/components/Navbar.svelte` — logo glow, aktywny link glow.
- `frontend/src/lib/styles/_buttons.scss` — paleta + glow.
- `frontend/src/lib/styles/_forms.scss` — focus w nowej palecie.
- `frontend/src/lib/components/Prose.svelte` — linki na niebieski, code retune (bez efektów VHS).
- `frontend/src/lib/components/ui/Modal.svelte` — fioletowy border-accent + subtelny glow nagłówka.

---

## Task 1: JetBrains Mono (self-hosted)

**Files:**
- Modify: `frontend/package.json` (+ `bun.lock`)
- Modify: `frontend/src/routes/+layout.svelte`
- Modify: `frontend/src/lib/styles/_variables.scss`
- Modify: `frontend/src/lib/styles/_reset.scss`

- [ ] **Step 1: Zainstaluj font (variable, self-hosted przez fontsource)**

Run (w `frontend/`):
```bash
bun add @fontsource-variable/jetbrains-mono
```

- [ ] **Step 2: Zaimportuj font w root layout**

W `frontend/src/routes/+layout.svelte`, w bloku `<script>`, dodaj jako PIERWSZY import (nad `import { page }`):
```ts
  import '@fontsource-variable/jetbrains-mono';
```

- [ ] **Step 3: Wskaź font w tokenie `$font-mono`**

W `frontend/src/lib/styles/_variables.scss` zmień linię `$font-mono: ...` na:
```scss
$font-mono: 'JetBrains Mono Variable', ui-monospace, 'Cascadia Code', 'SF Mono', Consolas, monospace;
```

- [ ] **Step 4: Domyślny font body → mono (CRT/terminal DNA)**

W `frontend/src/lib/styles/_reset.scss`, w regule `body`, zmień `font-family: v.$font-sans;` na:
```scss
  font-family: v.$font-mono;
```

- [ ] **Step 5: Weryfikacja (w tym polskie znaki)**

Run: `bun run check && bun run test`
Expected: 0 errors; 22 tests pass.
Run: `bun run dev` i sprawdź w przeglądarce, że tekst renderuje się w JetBrains Mono ORAZ że polskie znaki (np. „zażółć gęślą jaźń") wyświetlają się poprawnie (font zawiera latin-ext). Jeśli polskie znaki nie renderują się z webfontu, dodaj import subsetu: `import '@fontsource-variable/jetbrains-mono/latin-ext.css';` w `+layout.svelte` pod głównym importem.

- [ ] **Step 6: Commit**

```bash
bunx prettier --write src/routes/+layout.svelte src/lib/styles/_variables.scss src/lib/styles/_reset.scss
git add frontend/
git commit -m "feat: self-host JetBrains Mono as the mono typeface"
```

---

## Task 2: Migracja palety (VHS: złoto + fiolet + niebieski)

Zmiana wartości istniejących tokenów (nazwy bez zmian → auto-recolor) + nowe `$violet*`/`$blue*`.

**Files:**
- Modify: `frontend/src/lib/styles/_variables.scss`

- [ ] **Step 1: Zaktualizuj kolory tła/tekstu/ramek**

W `frontend/src/lib/styles/_variables.scss` zamień bloki kolorów na:
```scss
$bg-deep: #0b0912;
$bg-surface: #110d1c;
$bg-raised: #1a1430;
$text-primary: #ece8f5;
$text-secondary: #9a8fc0;
$text-muted: #5f5680;
$border: #241d3a;
$border-accent: #6a4fb0;
$accent: #f0b800;
$accent-hover: #ffc833;
$accent-dark: #9a7800;
$accent-light: #ffe08a;
$error: #ff4d6b;
```

- [ ] **Step 2: Dodaj akcenty fiolet/niebieski**

Tuż pod `$error: ...` dodaj:
```scss

$violet: #9d5cff;
$violet-bright: #b478ff;
$blue: #3d8bff;
$blue-bright: #6fa8ff;
```

- [ ] **Step 3: Przestrojenie gradientów (złoto→fiolet, fioletowa czerń)**

Zamień obie linie `$gradient-*` na:
```scss
$gradient-accent: linear-gradient(135deg, #ffc833 0%, #f0b800 35%, #9d5cff 100%);
$gradient-warm: linear-gradient(180deg, #160f2e 0%, #0b0912 60%);
```

- [ ] **Step 4: Weryfikacja**

Run: `bun run check && bun run test`
Expected: 0 errors; 22 tests pass. Cała aplikacja przebarwia się na nową paletę (tokeny współdzielone).

- [ ] **Step 5: Commit**

```bash
bunx prettier --write src/lib/styles/_variables.scss
git add frontend/src/lib/styles/_variables.scss
git commit -m "feat: VHS palette (gold + violet + blue) token migration"
```

---

## Task 3: Mixiny efektów `_effects.scss`

Partial z mixinami; jeszcze nieużywany (wpięcie w Task 4–6). Sam partial nie jest kompilowany dopóki nieużyty, więc weryfikacja to tylko poprawność składni przy pierwszym użyciu (Task 4).

**Files:**
- Create: `frontend/src/lib/styles/_effects.scss`

- [ ] **Step 1: Utwórz `_effects.scss`**

Create `frontend/src/lib/styles/_effects.scss`:
```scss
// Subtle CRT scanline overlay. Apply once to the app shell (body); draws a fixed
// full-viewport ::after layer that never intercepts pointer events.
@mixin scanlines {
  position: relative;

  &::after {
    content: '';
    position: fixed;
    inset: 0;
    z-index: 100;
    pointer-events: none;
    background: repeating-linear-gradient(
      0deg,
      rgba(0, 0, 0, 0.08) 0,
      rgba(0, 0, 0, 0.08) 1px,
      transparent 1px,
      transparent 3px
    );
  }
}

// Chromatic aberration — large headings / logo ONLY (never body or UI text).
@mixin aberration($offset: 1px) {
  text-shadow:
    #{-$offset} 0 rgba(157, 92, 255, 0.55),
    #{$offset} 0 rgba(61, 139, 255, 0.55);
}

// Soft box glow for accents / active states.
@mixin glow($glow-color, $strength: 0.45) {
  box-shadow: 0 0 10px rgba($glow-color, $strength);
}

// Soft text glow for headings / logo / links.
@mixin text-glow($glow-color, $strength: 0.5) {
  text-shadow: 0 0 8px rgba($glow-color, $strength);
}
```

- [ ] **Step 2: Sanity**

Run: `bun run check`
Expected: 0 errors (partial parsuje się; nieużywany jeszcze).

- [ ] **Step 3: Commit**

```bash
bunx prettier --write src/lib/styles/_effects.scss
git add frontend/src/lib/styles/_effects.scss
git commit -m "feat: VHS effect mixins (scanlines, aberration, glow)"
```

---

## Task 4: Scanlines na app-shell + efekty navbara

**Files:**
- Modify: `frontend/src/lib/styles/_reset.scss`
- Modify: `frontend/src/lib/styles/_typography.scss`
- Modify: `frontend/src/lib/components/Navbar.svelte`

- [ ] **Step 1: Scanlines na `body`**

W `frontend/src/lib/styles/_reset.scss`, na górze dodaj import obok istniejącego `@use 'variables' as v;`:
```scss
@use 'effects' as fx;
```
W regule `body`, jako ostatnią deklarację (po `-moz-osx-font-smoothing`), dodaj:
```scss
  @include fx.scanlines;
```

- [ ] **Step 2: Subtelna aberracja na `h1`**

W `frontend/src/lib/styles/_typography.scss`, dodaj import pod `@use 'variables' as v;`:
```scss
@use 'effects' as fx;
```
W regule `h1` dodaj jako ostatnią deklarację:
```scss
  @include fx.aberration(1px);
```

- [ ] **Step 3: Logo glow + aktywny link glow w navbarze**

W `frontend/src/lib/components/Navbar.svelte`, w `<style>`, dodaj pod `@use '$lib/styles/breakpoints' as bp;`:
```scss
  @use '$lib/styles/effects' as fx;
```
W regule `.navbar__logo`, jako ostatnią deklarację (po `transition: filter 0.15s;`), dodaj:
```scss
    @include fx.text-glow(v.$accent, 0.35);
```
W regule `.navbar__link--active`, jako ostatnią deklarację, dodaj:
```scss
      @include fx.glow(v.$accent, 0.25);
```

- [ ] **Step 4: Weryfikacja**

Run: `bun run check && bun run test`
Expected: 0 errors; 22 tests pass.
Manual (opcjonalnie): `bun run dev` — subtelne scanlines na całości, logo świeci na złoto, aktywny link „Notes" ma poświatę, nagłówki `h1` mają lekkie rozszczepienie. Treść notatki bez efektów (Prose tknięty dopiero w Task 6).

- [ ] **Step 5: Commit**

```bash
bunx prettier --write src/lib/styles/_reset.scss src/lib/styles/_typography.scss src/lib/components/Navbar.svelte
git add frontend/src/lib/styles/_reset.scss frontend/src/lib/styles/_typography.scss frontend/src/lib/components/Navbar.svelte
git commit -m "feat: apply scanlines + navbar glow/aberration"
```

---

## Task 5: Buttony + formularze w nowej palecie

**Files:**
- Modify: `frontend/src/lib/styles/_buttons.scss`
- Modify: `frontend/src/lib/styles/_forms.scss`

- [ ] **Step 1: Glow na primary buttonach**

W `frontend/src/lib/styles/_buttons.scss`, dodaj import na górze pod `@use 'variables' as v;`:
```scss
@use 'effects' as fx;
```
W regule `.btn-primary`, w bloku `&:hover:not(:disabled)`, zamień istniejący `box-shadow: 0 0 12px rgba(240, 184, 0, 0.35);` na:
```scss
    @include fx.glow(v.$accent, 0.4);
```
W regule `.btn` (system edytora), w bloku `&--primary`, dodaj `border-color`/glow — w `&:hover:not(:disabled)` dodaj jako ostatnią deklarację:
```scss
      @include fx.glow(v.$accent, 0.35);
```

- [ ] **Step 2: Secondary/ghost w fiolecie**

W `frontend/src/lib/styles/_buttons.scss`, w regule `.btn-ghost`, w bloku `&:hover`, zmień `border-color: v.$accent-dark;` na:
```scss
    border-color: v.$violet;
```
W regule `.btn` `&--secondary`, w bloku `&:hover`, zmień `background: rgba(255, 255, 255, 0.04);` na:
```scss
      background: rgba(157, 92, 255, 0.1);
```

- [ ] **Step 3: Focus formularzy w złoto/fiolet**

W `frontend/src/lib/styles/_forms.scss`, w regule `input` w bloku `&:focus`, zmień:
```scss
    border-color: v.$accent;
    box-shadow: 0 0 0 2px rgba(157, 92, 255, 0.25);
```
(czyli: border złoty, pierścień fioletowy zamiast złotego).

- [ ] **Step 4: Weryfikacja**

Run: `bun run check && bun run test`
Expected: 0 errors; 22 tests pass.

- [ ] **Step 5: Commit**

```bash
bunx prettier --write src/lib/styles/_buttons.scss src/lib/styles/_forms.scss
git add frontend/src/lib/styles/_buttons.scss frontend/src/lib/styles/_forms.scss
git commit -m "feat: buttons and form focus in VHS palette"
```

---

## Task 6: Prose (linki niebieskie, code) + Modal accent

Treść notatki zostaje **spokojna i jasna** — żadnych scanlines/aberracji/glow w `Prose`. Tylko paleta linków/kodu.

**Files:**
- Modify: `frontend/src/lib/components/Prose.svelte`
- Modify: `frontend/src/lib/components/ui/Modal.svelte`

- [ ] **Step 1: Linki w treści na niebieski**

W `frontend/src/lib/components/Prose.svelte`, w `<style>`:
- w `:global(a)` zmień `color: v.$accent;` na:
```scss
      color: v.$blue;
```
- w `:global(a:hover)` zmień `color: v.$accent-hover;` na:
```scss
      color: v.$blue-bright;
```

Uwaga: inline `code` w `Prose` używa `v.$accent-light`, którego wartość zmieniła się już w Task 2 (`#ffe08a`) — code automatycznie dostaje nowy kolor, bez edycji tutaj.

- [ ] **Step 2: Modal — fioletowy border-accent + subtelny glow nagłówka**

W `frontend/src/lib/components/ui/Modal.svelte`, w `<style>`, dodaj pod `@use '$lib/styles/breakpoints' as bp;`:
```scss
  @use '$lib/styles/effects' as fx;
```
W regule `.modal` (desktop), zmień `border: 1px solid v.$border;` na:
```scss
    border: 1px solid v.$border-accent;
```
W regule `.modal__title`, dodaj jako ostatnią deklarację:
```scss
    @include fx.text-glow(v.$violet, 0.3);
```

- [ ] **Step 3: Weryfikacja**

Run: `bun run check && bun run test`
Expected: 0 errors; 22 tests pass.
Manual (opcjonalnie): widok notatki — linki niebieskie, code złoto-kremowy na ciemnym, treść czytelna bez efektów; dialog „Przenieś" ma fioletowy obrys i delikatnie świecący tytuł.

- [ ] **Step 4: Commit**

```bash
bunx prettier --write src/lib/components/Prose.svelte src/lib/components/ui/Modal.svelte
git add frontend/src/lib/components/Prose.svelte frontend/src/lib/components/ui/Modal.svelte
git commit -m "feat: Prose links/code and Modal accent in VHS palette"
```

---

## Po fundamencie

Cała aplikacja ma nową paletę (fiolet+złoto+niebieski na fioletowej czerni), JetBrains Mono, subtelne scanlines, świecące akcenty/logo, fioletowe obrysy dialogów — przy zachowanej czytelności treści. 22 testy logiki zielone.

**Plan 2 (osobny):** restyl explorera/widoku notatki + responsywność tablet/desktop + `tagColor` (kodowanie tagów). **Plan 3:** reszta ekranów. Dedup prymitywów → issue #13.
