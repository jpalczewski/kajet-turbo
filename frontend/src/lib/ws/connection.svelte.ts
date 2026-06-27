import type { ServerEvent } from './protocol';

type Handler = (event: ServerEvent) => void;
type Status = 'connecting' | 'open' | 'closed';

let socket: WebSocket | null = null;
let _status = $state<Status>('closed');
let _backoff = 1000;
const _handlers: Handler[] = [];

function connect(): void {
  if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) {
    return;
  }
  _status = 'connecting';
  const ws = new WebSocket('/api/ws');

  ws.onopen = () => {
    socket = ws;
    _status = 'open';
    _backoff = 1000;
  };

  ws.onmessage = (e: MessageEvent) => {
    try {
      const event = JSON.parse(e.data as string) as ServerEvent;
      for (const h of _handlers) h(event);
    } catch {
      // ignore malformed messages
    }
  };

  ws.onclose = (e: CloseEvent) => {
    socket = null;
    _status = 'closed';
    if (e.code === 1008) return; // policy violation = unauthenticated, do not retry
    setTimeout(connect, _backoff);
    _backoff = Math.min(_backoff * 2, 30_000);
  };

  ws.onerror = () => ws.close();
}

function onEvent(handler: Handler): () => void {
  _handlers.push(handler);
  return () => {
    const i = _handlers.indexOf(handler);
    if (i > -1) _handlers.splice(i, 1);
  };
}

export const wsConnection = {
  connect,
  onEvent,
  get status(): Status {
    return _status;
  },
};
