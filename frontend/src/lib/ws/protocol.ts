export type NoteUpdatedEvent = {
  type: 'note_updated';
  owner_id: string;
  workspace: string;
  note_id: string;
  updated_at: string;
};

export type WorkspaceChangedEvent = {
  type: 'workspace_changed';
  owner_id: string;
  workspace: string;
};

// Extend this union when adding new server-to-client event types
export type ServerEvent = NoteUpdatedEvent | WorkspaceChangedEvent;

export type PingMessage = { type: 'ping' };
export type ClientMessage = PingMessage;

// A Record, not a list, so adding a member to ServerEvent fails to compile until it is here.
const KNOWN_TYPES: Record<ServerEvent['type'], true> = {
  note_updated: true,
  workspace_changed: true,
};

// Checks only the discriminant: the backend may add event types before the UI handles
// them, and those must be dropped here instead of reaching handlers under a wrong type.
export function parseServerEvent(raw: string): ServerEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== 'object' || data === null) return null;
  const type = (data as { type?: unknown }).type;
  return typeof type === 'string' && Object.hasOwn(KNOWN_TYPES, type)
    ? (data as ServerEvent)
    : null;
}
