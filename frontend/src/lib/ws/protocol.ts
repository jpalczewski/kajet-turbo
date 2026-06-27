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
