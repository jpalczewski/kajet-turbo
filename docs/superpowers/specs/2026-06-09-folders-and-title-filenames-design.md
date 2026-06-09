# Design: Foldery i nazwy plików od tytułu

**Data:** 2026-06-09  
**Status:** Approved

## Cel

Zmiana sposobu przechowywania notatek na dysku tak, aby workspace był prawdziwym repozytorium git z czytelnymi plikami markdown — pliki nazwane od tytułu, zorganizowane w dowolnie zagnieżdżone foldery.

Przed zmianą:
```
{workspace}/notes/{note_id}-{slug}.md
```

Po zmianie:
```
{workspace}/{folder}/{WindowsTitle}.md
```

## Struktura plików w git

Notatki żyją bezpośrednio w workspace root lub w podfolderach. Brak płaskiego katalogu `notes/`.

```
{workspace}/
  Projekty/
    Klient A/
      Spotkanie kickoff.md
      Notatki z demo.md
    README.md
  Dziennik/
    2024-01.md
  Luźne przemyślenia.md
```

## Model danych

### Zmiana w `Note` (models.py)

Dodaj pole:
```python
folder: str = Field(default="")
```

Semantyka `folder`:
- `""` — root workspace
- `"Projekty/Klient A"` — zagnieżdżony folder
- Separator: `/` (normalizowany na backendzie niezależnie od OS)
- Nie zaczyna się ani nie kończy na `/`

### Migracja Alembic

Nowa kolumna: `ALTER TABLE notes ADD COLUMN folder TEXT NOT NULL DEFAULT ""`

Brak migracji plików — środowisko produkcyjne nie ma istniejących notatek.

## Nazewnictwo plików

### Funkcja `title_to_windows_filename(title: str) -> str`

Zastępuje obecny `title_to_slug`. Zasady sanityzacji:

1. Usuń znaki zakazane na Windows: `\ / : * ? " < > |` i znaki kontrolne (ord < 32)
2. Zastąp usuniętymi miejscami spacją, potem trim
3. Usuń trailing spacje i kropki (Windows nie pozwala)
4. Jeśli wynik to reserved name Windows (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) — dodaj prefix `_`
5. Jeśli wynik pusty — użyj `"untitled"`
6. Max 200 znaków

Przykłady:
- `"Spotkanie: kickoff"` → `"Spotkanie kickoff"`
- `"Notatki z demo"` → `"Notatki z demo"`
- `"CON"` → `"_CON"`
- `""` → `"untitled"`

### Ścieżka pliku

```python
def note_filepath(ws_path: str, folder: str, title: str) -> str:
    filename = title_to_windows_filename(title) + ".md"
    parts = [p for p in folder.split("/") if p]
    return str(Path(ws_path, *parts, filename))
```

## Unikalność

Serwis blokuje tworzenie notatki gdy już istnieje notatka z tym samym `(workspace, owner_id, folder, title)` — zwraca 409. Sufiksy `-2`, `-3` tylko jako ostateczny fallback (implementacja opcjonalna, w praktyce serwis wymusza unikalność przed zapisem).

## API

### POST `/api/workspaces/{name}/notes`

Request body:
```json
{
  "title": "Spotkanie kickoff",
  "content": "...",
  "tags": [],
  "folder": "Projekty/Klient A"
}
```
- `folder` opcjonalne, domyślnie `""`

Response: `{"note_id": "abc1234"}`

### PATCH `/api/workspaces/{name}/notes/{note_id}`

Request body (wszystkie pola opcjonalne):
```json
{
  "title": "Nowy tytuł",
  "folder": "Nowy/Folder",
  "content": "...",
  "tags": []
}
```

Gdy zmienia się `title` lub `folder` (lub oba): wykonuje `git mv` stara ścieżka → nowa ścieżka w jednej operacji.

### GET `/api/workspaces/{name}/notes`

Query param: `?folder=Projekty/Klient A` (opcjonalny filtr).

Każda notatka w odpowiedzi zawiera pole `folder`.

### Pozostałe endpointy

Wszystkie odpowiedzi zawierające notatkę (GET html, GET markdown, itp.) zwracają `folder`.

## Implementacja — lista zmian

### `workspace.py`

- Usuń `title_to_slug`
- Dodaj `title_to_windows_filename(title: str) -> str`
- Zmień sygnaturę `note_filepath(ws_path, folder, title)` (bez `note_id`)
- `scan_notes` — zmień glob z `notes/*.md` na `**/*.md` (rekurencyjny)

### `git_ops.py`

Dodaj:
```python
def rename_file_commit(workspace_path: str, old_rel: str, new_rel: str, message: str) -> None:
    repo = Repo(workspace_path)
    repo.index.move([old_rel, new_rel])
    repo.index.commit(message)
```

### `models.py`

Dodaj `folder: str = Field(default="")` do `Note`.

### `repositories/notes.py`

- `insert()` — przyjmuje i zapisuje `folder`
- `list()` — zwraca `folder`, obsługuje opcjonalny filtr `folder`
- Dodaj `check_unique(workspace, owner_id, folder, title) -> bool`
- Aktualizacja: `update()` przyjmuje opcjonalny `folder`

### `services/notes.py`

- `save()`:
  - Przyjmuje `folder`
  - Wywołuje `check_unique` → 409 jeśli duplikat
  - Używa nowej `note_filepath(ws_path, folder, title)`
  - `mkdir -p` dla katalogu docelowego
- `update()`:
  - Jeśli zmienił się `title` lub `folder`: wywołuje `rename_file_commit`
  - Ścieżka pliku pochodzi z DB (`folder` + `title`), nie z glob po ID
- `get_with_content()` — ścieżka z DB zamiast `glob(f"{note_id}-*.md")`
- `delete()` — ścieżka z DB
- `reindex()` — skanuje `**/*.md`, czyta frontmatter każdego pliku

### `api/workspaces.py`

- POST notes — czyta `folder` z body
- PATCH notes — czyta `folder` z body
- Wszystkie odpowiedzi z notatkami zawierają `folder`

## Decyzje projektowe

- **Folder jako ścieżka string w DB** — prosta implementacja, brak osobnego modelu foldera. Foldery powstają automatycznie przy tworzeniu notatki.
- **Brak płaskiego `notes/`** — notatki bezpośrednio w workspace root lub podfolderach, git repo wygląda jak prawdziwy vault markdown.
- **`git mv` przy rename/move** — zachowuje historię git pliku.
- **Sanityzacja Windows** — pliki działają na Windows, macOS i Linux.
- **DB jako source of truth dla lokalizacji pliku** — serwis zawsze wie gdzie jest plik bez skanowania.
