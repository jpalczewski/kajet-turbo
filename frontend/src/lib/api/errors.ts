const ERROR_MESSAGES: Record<string, string> = {
  NOT_AUTHENTICATED: 'Zaloguj się.',
  ACCESS_DENIED: 'Brak dostępu.',
  WORKSPACE_NOT_FOUND: 'Workspace nie istnieje.',
  WORKSPACE_ALREADY_EXISTS: 'Workspace o tej nazwie już istnieje.',
  WORKSPACE_NAME_REQUIRED: "Nazwa workspace'u jest wymagana.",
  WORKSPACE_INVALID_INPUT: "Nieprawidłowe dane workspace'u.",
  NOTE_NOT_FOUND: 'Notatka nie istnieje.',
  NOTE_ALREADY_EXISTS: 'Notatka o tym tytule już istnieje.',
  NOTE_TITLE_REQUIRED: 'Tytuł jest wymagany.',
  BROKEN_WIKILINK: 'Nieznany wikilink.',
  NOTE_INVALID_INPUT: 'Nieprawidłowe dane notatki.',
  FOLDER_PATH_REQUIRED: 'Ścieżka jest wymagana.',
  FOLDER_PATH_INVALID: 'Niedozwolona ścieżka.',
  INVALID_FOLDER: 'Nieprawidłowy folder.',
  FOLDER_NOT_FOUND: 'Folder nie istnieje.',
  GIT_ERROR: 'Błąd git.',
  INTERNAL_ERROR: 'Błąd wewnętrzny.',
};

export function translateErrorCode(code: string | undefined): string | undefined {
  if (!code) return undefined;
  return ERROR_MESSAGES[code];
}
