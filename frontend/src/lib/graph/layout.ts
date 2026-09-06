import forceAtlas2 from 'graphology-layout-forceatlas2';
import FA2LayoutSupervisor from 'graphology-layout-forceatlas2/worker';
import type { NoteGraph } from './model';

export interface LayoutHandle {
  start(): void;
  stop(): void;
  kill(): void;
  isRunning(): boolean;
}

export interface LayoutOptions {
  /** How long the worker keeps iterating before auto-stopping. The worker variant has no
   * iteration-count knob (unlike the synchronous API) — it runs until stopped, so bounding
   * runtime is how this caps cost for ~1000-node graphs. */
  durationMs?: number;
}

const DEFAULT_DURATION_MS = 3000;

/** Owns the force-directed simulation, isolated from rendering and from the Svelte component.
 * #138 (workspace-wide view) needs a progressive/incremental layout strategy at ~1000 nodes —
 * keeping layout behind this seam lets it swap the strategy without touching GraphView.svelte. */
export function createLayout(graph: NoteGraph, options: LayoutOptions = {}): LayoutHandle {
  const settings = forceAtlas2.inferSettings(graph);
  const supervisor = new FA2LayoutSupervisor(graph, { settings });
  const durationMs = options.durationMs ?? DEFAULT_DURATION_MS;
  let stopTimer: ReturnType<typeof setTimeout> | undefined;

  return {
    start() {
      supervisor.start();
      stopTimer = setTimeout(() => supervisor.stop(), durationMs);
    },
    stop() {
      clearTimeout(stopTimer);
      supervisor.stop();
    },
    kill() {
      clearTimeout(stopTimer);
      supervisor.kill();
    },
    isRunning() {
      return supervisor.isRunning();
    },
  };
}
