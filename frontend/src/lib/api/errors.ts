const ERROR_MESSAGES: Record<string, string> = {
  NOT_AUTHENTICATED: 'Zaloguj się.',
  ACCESS_DENIED: 'Brak dostępu.',
  INVALID_CREDENTIALS: 'Nieprawidłowy email lub hasło.',
  PENDING_EXPIRED: 'Wygasły pending_id.',
  WORKSPACE_NOT_FOUND: 'Workspace nie istnieje.',
  WORKSPACE_ALREADY_EXISTS: 'Workspace o tej nazwie już istnieje.',
  WORKSPACE_NAME_REQUIRED: "Nazwa workspace'u jest wymagana.",
  WORKSPACE_INVALID_INPUT: "Nieprawidłowe dane workspace'u.",
  WORKSPACE_BACKFILL_STALE: 'Dane zmieniły się w międzyczasie — uruchom analizę ponownie.',
  NOTE_NOT_FOUND: 'Notatka nie istnieje.',
  NOTE_ALREADY_EXISTS: 'Notatka o tym tytule już istnieje.',
  NOTE_TITLE_REQUIRED: 'Tytuł jest wymagany.',
  BROKEN_WIKILINK: 'Nieznany wikilink.',
  NOTE_INVALID_INPUT: 'Nieprawidłowe dane notatki.',
  NOTE_STALE_VERSION:
    'Notatka została zmieniona w międzyczasie — odśwież stronę i spróbuj ponownie.',
  FOLDER_PATH_REQUIRED: 'Ścieżka jest wymagana.',
  FOLDER_PATH_INVALID: 'Niedozwolona ścieżka.',
  INVALID_FOLDER: 'Nieprawidłowy folder.',
  FOLDER_NOT_FOUND: 'Folder nie istnieje.',
  GIT_ERROR: 'Błąd git.',
  JOB_NOT_FOUND: 'Zadanie nie istnieje.',
  INTERNAL_ERROR: 'Błąd wewnętrzny.',
  INVALID_INPUT: 'Nieprawidłowe dane.',
  WORKSPACE_REMOTE_NOT_FOUND: 'Remote nie jest skonfigurowany.',
  WORKSPACE_REMOTE_NOT_CONFIGURED: 'Brak włączonego remote.',
  WORKSPACE_REMOTE_INVALID_INPUT: 'Nieprawidłowe dane remote.',
  PREFERENCES_INVALID_INPUT: 'Nieprawidłowe dane preferencji.',
  SSH_KEY_NAME_REQUIRED: 'Nazwa klucza jest wymagana.',
  SSH_KEY_NAME_TAKEN: 'Klucz o tej nazwie już istnieje.',
  SSH_KEY_INVALID_ALGORITHM: 'Nieobsługiwany algorytm klucza.',
  SSH_KEY_NOT_FOUND: 'Klucz nie istnieje.',
  EMBEDDING_PROFILE_NOT_FOUND: 'Profil nie istnieje.',
  EMBEDDING_PROFILE_PROBE_FAILED: 'Nie udało się połączyć z embedderem.',
  SHARE_LINK_NOT_FOUND: 'Link nie istnieje.',
};

export function translateErrorCode(code: string | undefined): string | undefined {
  if (!code) return undefined;
  return ERROR_MESSAGES[code];
}
