import type { NoteItem, SessionResponse, WorkspaceInfo } from '$lib/api';

declare global {
  namespace App {
    interface PageData {
      session: SessionResponse | null;
      workspaces?: WorkspaceInfo[];
      notes?: NoteItem[];
    }
  }
}

export {};
