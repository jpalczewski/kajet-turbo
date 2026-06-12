import type { NoteItem, WorkspaceInfo } from '$lib/api';

declare global {
  namespace App {
    interface PageData {
      session: { email: string } | null;
      workspaces?: WorkspaceInfo[];
      notes?: NoteItem[];
    }
  }
}

export {};
