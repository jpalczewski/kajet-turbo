# kajet-turbo

RAG dla notatek markdown — dostęp z Claude mobile via MCP OAuth.

## Uruchomienie

```bash
uv sync
MCP_BASE_URL=http://localhost:8000 kajet-turbo
```

## Zmienne środowiskowe

### Wymagane

| Zmienna | Opis |
|---|---|
| `MCP_BASE_URL` | Publiczny URL serwera (np. `https://kajet.example.com`). Alternatywnie Coolify ustawi `COOLIFY_FQDN` lub `COOLIFY_URL`. |

### Serwer

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `MCP_HOST` | `0.0.0.0` | Adres nasłuchu |
| `MCP_PORT` | `8000` | Port nasłuchu |
| `KAJET_ROLE` | `all` | Rola procesu: `all` (MCP+API+SPA w jednym — dev), `mcp` (tylko `/mcp` + OAuth, **zawsze 1 worker**), `api` (REST `/api` + SPA, N workerów) |
| `MCP_WORKERS` | `1` | Liczba workerów dla roli `all` |
| `API_WORKERS` | `2` | Liczba workerów dla roli `api` |

Topologia produkcyjna (`docker-compose.yml`): ingress (Caddy) + `kajet-api`
(stateless, N workerów) + `kajet-mcp` (stateful, 1 worker — sesje MCP i
`ctx.sample()` wymagają jednego procesu). Obie role współdzielą wolumeny `/data`
(SQLite) i `/workspaces` (git) **na tym samym hoście**. Host-proxy kieruje tylko
`Host → ingress:8000`; podział ścieżek robi `Caddyfile`.

### Dane

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `DB_PATH` | `/data/kajet.db` | Ścieżka do bazy SQLite |
| `WORKSPACES_DIR` | `/workspaces` | Katalog główny workspace'ów |
| `EMBEDDING_DIM` | `1536` | Wymiar embeddingów (musi pasować do modelu) |

### Inicjalizacja

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `KAJET_ADMIN_EMAIL` | — | Email konta admin (tworzone przy pierwszym starcie) |
| `KAJET_ADMIN_PASSWORD` | — | Hasło konta admin |

### Logowanie

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Poziom logów (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_SQL` | — | Ustaw dowolną wartość żeby logować zapytania SQL (`LOG_SQL=1`) |

Logi są emitowane na stderr w formacie JSONL. Przykłady:

```bash
# produkcja — tylko INFO, bez SQL
kajet-turbo

# debug — pełne logi aplikacji
LOG_LEVEL=DEBUG kajet-turbo

# śledzenie zapytań SQL
LOG_SQL=1 kajet-turbo

# pełny debug z SQL
LOG_LEVEL=DEBUG LOG_SQL=1 kajet-turbo 2> debug.jsonl
```
