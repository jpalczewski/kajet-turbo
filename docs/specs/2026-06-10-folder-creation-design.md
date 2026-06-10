# Tworzenie folderów z frontendu

**Data:** 2026-06-10

## Cel

Umożliwienie użytkownikowi tworzenia folderów bezpośrednio z eksploratora notatek, bez konieczności tworzenia najpierw notatki.

## Decyzje projektowe

| Pytanie | Odpowiedź |
|---|---|
| Gdzie trigger "+"? | Przy nagłówku workspace w lewym panelu (FolderTree) |
| UX wpisywania nazwy | Inline input bezpośrednio w drzewie folderów |
| Kontekst tworzenia | W aktualnie wybranym folderze (`currentFolder`) |
| Backend | Plik `.gitkeep` commitowany do gita, nowy API endpoint |
| Zagnieżdżone ścieżki | Tak — `a/b/c` tworzy pełną strukturę |

## Backend

### Nowy endpoint

```
POST /workspaces/{name}/folders
```

**Body:**
```json
{ "path": "docs/api/v1" }
```

- `path` to pełna ścieżka od roota workspace (frontend skleja `currentFolder + "/" + wpisana_ścieżka`)
- Tworzy `<ws_path>/<path>/.gitkeep` z `Path.mkdir(parents=True, exist_ok=True)`
- Git commituje plik: `"folder: add <path>"`
- Walidacja: brak `..`, brak pustych segmentów, dozwolone tylko `[a-zA-Z0-9._\-/]`
- Jeśli folder już istnieje → 200 OK (idempotentne)
- Path traversal → 422
- Pusty segment (`a//b`) → 422

**Response:**
```json
{ "path": "docs/api/v1" }
```

### Pliki do zmiany

- `src/kajet_turbo/api/workspaces.py` — nowy router endpoint
- `src/kajet_turbo/api/schemas.py` — nowy schemat `CreateFolderRequest` / `CreateFolderResponse`
- `openapi.json` — regeneracja po zmianach API

## Frontend

### FolderTree.svelte

Nowy prop:
```ts
onCreateFolder: (path: string) => Promise<void>
```

Nowy stan lokalny:
```ts
let creatingIn: string | null = $state(null)
```

Zachowanie:
- Ikona `+` obok nazwy workspace w nagłówku drzewa
- Kliknięcie `+` → `creatingIn = currentFolder`
- Pod aktywnym folderem (lub na poziomie root) pojawia się inline input
- `Enter` → skleja ścieżkę (`currentFolder ? currentFolder + "/" + input : input`) → wywołuje `onCreateFolder(fullPath)` → czyści `creatingIn`
- `Escape` → czyści `creatingIn`

Walidacja na frontendzie:
- Pusty input → `Enter` nic nie robi
- Niedozwolone znaki → komunikat pod inputem, request nie jest wysyłany
- Błąd API (422) → czerwony komunikat pod inputem, input zostaje otwarty

### +page.svelte

Obsługuje callback `onCreateFolder`:
1. Wywołuje `POST /workspaces/{slug}/folders` przez Orval-wygenerowaną funkcję
2. Po sukcesie: `invalidate()` danych + `goto` do nowego folderu
3. Przy błędzie: przekazuje błąd z powrotem do `FolderTree`

### Orval

Po dodaniu endpointu, regeneracja typów: `pnpm orval` (lub odpowiednik skryptu w projekcie).

## Testowanie

Nowe testy backendowe w `tests/`:
- Tworzenie prostego folderu
- Tworzenie zagnieżdżonej ścieżki (`a/b/c`)
- Idempotentność (folder już istnieje → 200)
- Path traversal → 422
- Pusty segment → 422

Testy frontendowe: brak (projekt nie ma ich aktualnie).

## Zakres

Tylko tworzenie folderów. Poza zakresem tego speca: usuwanie, zmiana nazwy, przenoszenie folderów.
