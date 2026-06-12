import type { NoteItem } from '$lib/types';

declare global {
  namespace App {
    interface PageData {
      session: { email: string } | null;
      workspaces?: string[];
      notes?: NoteItem[];
    }
  }
}

export {};
