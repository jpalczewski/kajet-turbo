import { afterEach, describe, expect, it, vi } from 'vitest';

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static last: FakeWebSocket | undefined;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    FakeWebSocket.last = this;
  }
  close(): void {}
}

vi.stubGlobal('WebSocket', FakeWebSocket);
const { wsConnection } = await import('./connection.svelte');
wsConnection.connect();
const send = (data: string) => FakeWebSocket.last!.onmessage!({ data });

const event = { type: 'workspace_changed', owner_id: 'u1', workspace: 'ws-1' };
const unsubscribers: Array<() => void> = [];
const subscribe = (handler: (e: unknown) => void) =>
  unsubscribers.push(wsConnection.onEvent(handler));

afterEach(() => {
  unsubscribers.splice(0).forEach((u) => u());
  vi.restoreAllMocks();
});

describe('wsConnection dispatch', () => {
  it('delivers an event to every handler even when one throws', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const before = vi.fn();
    const after = vi.fn();
    subscribe(before);
    subscribe(() => {
      throw new Error('boom');
    });
    subscribe(after);

    send(JSON.stringify(event));

    expect(before).toHaveBeenCalledWith(event);
    expect(after).toHaveBeenCalledWith(event);
    expect(console.error).toHaveBeenCalledOnce();
  });

  it('lets a handler unsubscribe itself without skipping the next one', () => {
    const next = vi.fn();
    const off = wsConnection.onEvent(() => off());
    subscribe(next);

    send(JSON.stringify(event));

    expect(next).toHaveBeenCalledOnce();
  });

  it('does not dispatch malformed or unknown messages', () => {
    const handler = vi.fn();
    subscribe(handler);

    send('{not json');
    send(JSON.stringify({ type: 'notices_changed', owner_id: 'u1' }));

    expect(handler).not.toHaveBeenCalled();
  });
});
