# RAG MCP — Design Spec

**Data:** 2026-06-08  
**Status:** Approved

---

## Cel

Rozszerzenie serwera `kajet-turbo` o RAG po notatkach markdown. Notatki tworzone przez Claude via MCP, wersjonowane w gicie, przeszukiwane semantycznie (sqlite-vec) i pełnotekstowo (FTS5). Dostęp z Claude Mobile przez OAuth.

---

## Architektura

```
Claude Mobile
     │ MCP OAuth
     ▼
FastMCP server (server.py)
     │
     ├── activate_workspace(name)
     ├── list_workspaces()
     ├── save_note / get_note / update_note / delete_note
     ├── search_notes(query, workspace?)
     ├── list_notes(tags?, limit?)
     └── reindex_workspace()
     │
     ├── Storage layer (storage.py)
     │     └── /data/kajet.db  ← jedna baza SQLite
     │
     └── Git layer (git_ops.py)
           └── /workspaces/{name}/  ← każdy workspace to osobny git repo
                 └── notes/{id}-{slug}.md
```

**Aktywny workspace** jest stanem sesji MCP (`ctx.set_state` / `ctx.get_state`). Ephemeral — ginie po rozłączeniu, Claude wywołuje `activate_workspace` na początku konwersacji.

---

## Baza danych

Jedna baza: `/data/kajet.db` (Docker volume `/data`).

### Schema

```sql
-- OAuth — persystencja dynamicznie rejestrowanych klientów
CREATE TABLE oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_secret TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,   -- JSON array
    created_at    TEXT NOT NULL
);

-- Użytkownicy (gotowość na multi-tenant)
CREATE TABLE users (
    id         TEXT PRIMARY KEY,   -- nanoid
    email      TEXT UNIQUE,
    created_at TEXT NOT NULL
);

-- Uprawnienia do workspace'ów
CREATE TABLE workspace_access (
    user_id   TEXT NOT NULL REFERENCES users(id),
    workspace TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'owner',  -- owner | reader
    PRIMARY KEY (user_id, workspace)
);

-- Metadane notatek
CREATE TABLE notes (
    id         TEXT PRIMARY KEY,   -- nanoid (7 znaków)
    workspace  TEXT NOT NULL,
    title      TEXT NOT NULL,
    tags       TEXT,               -- JSON array
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Wektory (sqlite-vec) — workspace jako partition key
CREATE VIRTUAL TABLE notes_vec USING vec0(
    note_rowid INTEGER PRIMARY KEY,
    embedding  float[N],           -- N ustalane przy init (zależne od modelu)
    workspace  TEXT partition key,
    note_id    TEXT                -- nanoid, metadata column do joina
);

-- Full-text search (FTS5) — trigram dla PL/EN
CREATE VIRTUAL TABLE notes_fts USING fts5(
    note_id   UNINDEXED,
    workspace UNINDEXED,
    title,
    content,
    content='',
    tokenize='trigram'
);
```

### Pliki notatek

Lokalizacja: `/workspaces/{name}/notes/{id}-{title-kebab}.md`

```markdown
---
id: 01J5K2X
title: Tytuł notatki
tags: [python, mcp]
created_at: 2026-06-08T14:30:00Z
updated_at: 2026-06-08T14:30:00Z
---

Treść notatki w markdown...
```

Source of truth to zawsze plik `.md` w gicie. SQLite jest indeksem pochodnym — można go w całości przebudować przez `reindex_workspace()`.

---

## MCP Tools

```python
activate_workspace(name: str) -> str
    # Ustawia aktywny workspace w sesji (ctx.set_state).
    # Błąd jeśli /workspaces/{name} nie istnieje.

list_workspaces() -> list[str]
    # Skanuje /workspaces/*/, zwraca nazwy.

save_note(title: str, content: str, tags: list[str] = []) -> str
    # Zwraca id nowej notatki.

get_note(id: str) -> Note
    # Czyta plik .md, parsuje frontmatter.

update_note(id: str, title: str = None, content: str = None, tags: list[str] = None) -> str

delete_note(id: str) -> str

search_notes(query: str, workspace: str = "active", limit: int = 10) -> list[Note]
    # workspace="active" → bierze z session state
    # workspace="all"    → szuka we wszystkich workspace'ach

list_notes(tags: list[str] = [], limit: int = 20) -> list[Note]

reindex_workspace() -> str
    # Przebudowuje indeks SQLite z plików .md.
    # Idempotentne. Regeneruje notes, notes_fts.
    # Regeneruje notes_vec jeśli model embeddingów dostępny.
```

---

## Data flow

### Zapis notatki

```
save_note(title, content, tags)
  1. generuj nanoid → id
  2. zapisz /workspaces/{name}/notes/{id}-{slug}.md  (temp + rename dla atomowości)
  3. git add notes/{id}-{slug}.md
  4. git commit -m "note: add {title}"
  5. INSERT INTO notes (id, workspace, title, tags, ...)
  6. INSERT INTO notes_vec (note_rowid, embedding=NULL, workspace)
  7. INSERT INTO notes_fts (note_id, workspace, title, content)
```

Embedding jest `NULL` — slot zarezerwowany. Wyszukiwanie wektorowe aktywne dopiero po podpięciu modelu embeddingów i uruchomieniu `reindex_workspace()`.

### Wyszukiwanie

```
search_notes(query, workspace, limit)
  1. jeśli embedding dostępny → embed(query) → hybrid search (CTE poniżej)
  2. jeśli embedding niedostępny → fallback na FTS5-only
  3. zwróć listę Note
```

```sql
WITH fts_results AS (
    SELECT note_id, rank
    FROM notes_fts
    WHERE notes_fts MATCH ? AND workspace = ?
),
vec_results AS (
    SELECT note_rowid, distance
    FROM notes_vec
    WHERE embedding MATCH ? AND k = 20 AND workspace = ?
)
SELECT n.id, n.title, n.tags
FROM notes n
JOIN (
    SELECT note_id FROM fts_results
    UNION ALL
    SELECT note_rowid FROM vec_results
) combined ON n.id = combined.note_id
GROUP BY n.id
ORDER BY COUNT(*) DESC
LIMIT ?
```

### Reindex

```
reindex_workspace()
  1. skanuj /workspaces/{name}/notes/*.md
  2. parsuj frontmatter każdego pliku
  3. upsert do notes (INSERT OR REPLACE)
  4. rebuild notes_fts (DELETE + INSERT)
  5. jeśli model embeddingów dostępny → rebuild notes_vec
```

---

## Error handling

- `activate_workspace` z nieistniejącą nazwą → `ValueError` z listą dostępnych workspace'ów
- `git commit` failure → rollback (usuń temp file), zwróć błąd z detalami
- `search_notes` bez embeddingów → automatyczny fallback na FTS5, bez błędu użytkownika
- Brak aktywnego workspace w sesji → czytelny komunikat: "Wywołaj activate_workspace() najpierw"
- `reindex_workspace` → idempotentne, bezpieczne do wielokrotnego wywołania

---

## Testowanie

- Testy integracyjne z prawdziwym SQLite (`:memory:` dla szybkości)
- Fixture: `tmp_path` z `git init` jako fake workspace
- Kluczowe scenariusze:
  - save → search → update → delete
  - reindex odtwarza stan z plików .md
  - hybrid search fallback gdy embedding=NULL
  - cross-workspace search (`workspace="all"`)
  - FTS5 trigram dla polskich znaków

---

## Moduły

```
src/kajet_turbo/
├── server.py          ← istniejący, dodajemy tools
├── auth.py            ← istniejący, refactor na persystencję OAuth
├── storage.py         ← NOWY: operacje na SQLite (notes + global)
├── git_ops.py         ← NOWY: commit, rollback
└── workspace.py       ← NOWY: skanowanie /workspaces/, session state
```

---

## Zależności do dodania

```toml
dependencies = [
    "fastmcp>=3.2.0",
    "sqlite-vec>=0.1.9",
    "python-frontmatter>=1.1.0",
    "nanoid>=2.0.0",
    "gitpython>=3.1.0",
]
```

---

## Poza zakresem tego speca

- Model embeddingów (pluggable, `NULL` do czasu)
- UI / web interface
- Sync notatek z zewnętrznych źródeł (Obsidian, itp.)
