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

## Obrazy developerskie

CI buduje finalne targety Dockerfile dla `linux/amd64` i `linux/arm64`:

- `ghcr.io/jpalczewski/kajet-turbo-app`
- `ghcr.io/jpalczewski/kajet-turbo-ingress`

Pull requesty tylko budują, walidują i skanują obrazy. Push do `main` publikuje
obrazy, które przeszły skan Trivy, pod niezmiennym tagiem `sha-<commit>`. Po
udanym zbudowaniu obu targetów dla obu architektur CI przesuwa także wygodny,
ruchomy tag `develop`.

Przy pierwszej publikacji ustaw oba pakiety na `Public` w ich ustawieniach na
GitHubie. Workflow sprawdza anonimowy odczyt obu obrazów przed przesunięciem
`develop`, więc prywatny pakiet zatrzyma promocję; po zmianie widoczności można
bezpiecznie ponowić ten sam run.

```bash
# Najnowsza poprawna wersja developerska
docker pull ghcr.io/jpalczewski/kajet-turbo-app:develop
docker pull ghcr.io/jpalczewski/kajet-turbo-ingress:develop

# Odtwarzalna para obrazów z jednego commitu
docker pull ghcr.io/jpalczewski/kajet-turbo-app:sha-<commit>
docker pull ghcr.io/jpalczewski/kajet-turbo-ingress:sha-<commit>
```

Do wdrożeń lub debugowania pary usług używaj tego samego tagu `sha-<commit>`
dla obu obrazów. GHCR nie aktualizuje dwóch pakietów transakcyjnie, więc
`develop` jest wyłącznie skrótem do codziennej pracy.

Każdy wariant platformowy ma CycloneDX SBOM i provenance podpisane przez
GitHub Actions. Atestację indeksu multi-arch można zweryfikować tak:

```bash
gh attestation verify \
  oci://ghcr.io/jpalczewski/kajet-turbo-app:sha-<commit> \
  -R jpalczewski/kajet-turbo
```

SBOM jest przypięty do konkretnego manifestu platformowego (`amd64` lub
`arm64`):

```bash
gh attestation verify \
  oci://ghcr.io/jpalczewski/kajet-turbo-app:sha-<commit>-arm64 \
  -R jpalczewski/kajet-turbo \
  --predicate-type https://cyclonedx.org/bom
```

Raz w tygodniu CI ponownie skanuje opublikowane obrazy `develop`, aby wykrywać
podatności ujawnione już po publikacji. Obrazy CI są artefaktami developerskimi:
workflow nie wywołuje deploymentu i nie przekazuje ich do Coolify. Coolify
pozostaje źródłem prawdy dla produkcji i sam buduje oba targety z Dockerfile.

Wąskie wyjątki od bramki Trivy znajdują się w `.trivyignore.yaml`. Każdy jest
ograniczony do konkretnego pakietu lub ścieżki oraz ma datę wygaśnięcia;
wyjątek bez tych ograniczeń nie powinien być dodawany.

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
