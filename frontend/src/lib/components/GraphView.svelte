<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import Sigma from 'sigma';
  import type { GraphResponse } from '$lib/api';
  import { buildGraph, nodeDetail, nodeLabel, randomizePositions } from '$lib/graph/model';
  import type {
    AnyGraphNode,
    GraphEdgeAttributes,
    GraphNodeAttributes,
    NoteGraph,
  } from '$lib/graph/model';
  import { createLayout } from '$lib/graph/layout';
  import type { LayoutHandle } from '$lib/graph/layout';
  import { edgeReducer, nodeReducer } from '$lib/graph/style';

  type NoteSigma = Sigma<GraphNodeAttributes, GraphEdgeAttributes>;

  let {
    data,
    onNodeClick,
    onNodeHover,
  }: {
    data: GraphResponse;
    onNodeClick?: (node: AnyGraphNode) => void;
    onNodeHover?: (node: AnyGraphNode | null) => void;
  } = $props();

  let container: HTMLDivElement;
  let renderer: NoteSigma | null = null;
  let graph: NoteGraph | null = null;
  let layout: LayoutHandle | null = null;
  let resizeObserver: ResizeObserver | null = null;

  let hoveredNode = $state<string | null>(null);
  let selectedNode = $state<string | null>(null);
  let tooltipPos = $state<{ x: number; y: number } | null>(null);

  const activeNodeId = $derived(hoveredNode ?? selectedNode);
  const activeSource = $derived(activeNodeId ? nodeSource(activeNodeId) : null);

  $effect(() => {
    onNodeHover?.(activeSource);
  });

  function nodeSource(id: string): AnyGraphNode | null {
    return graph?.hasNode(id) ? graph.getNodeAttribute(id, 'source') : null;
  }

  // Sigma normalizes mouse and touch input into the same node events; the underlying event
  // still exposes which device fired it, which is how desktop hover vs. mobile tap-to-select
  // is told apart below.
  function isTouchOriginated(original: MouseEvent | TouchEvent): boolean {
    return typeof TouchEvent !== 'undefined' && original instanceof TouchEvent;
  }

  function updateTooltipPosition(sigma: NoteSigma, nodeId: string) {
    const display = sigma.getNodeDisplayData(nodeId);
    if (display) tooltipPos = sigma.framedGraphToViewport(display);
  }

  function clearSelection() {
    selectedNode = null;
    tooltipPos = null;
  }

  function wireEvents(sigma: NoteSigma) {
    sigma.on('enterNode', ({ node, event }) => {
      if (isTouchOriginated(event.original)) return;
      hoveredNode = node;
      updateTooltipPosition(sigma, node);
    });

    sigma.on('leaveNode', ({ event }) => {
      if (isTouchOriginated(event.original)) return;
      hoveredNode = null;
      tooltipPos = null;
    });

    sigma.on('clickNode', ({ node, event }) => {
      if (isTouchOriginated(event.original)) {
        // First tap selects (mirrors desktop hover); a second tap on the same node navigates —
        // a single tap acting as instant navigation would make the tooltip pointless on touch.
        if (selectedNode === node) {
          const source = nodeSource(node);
          clearSelection();
          if (source) onNodeClick?.(source);
        } else {
          selectedNode = node;
          updateTooltipPosition(sigma, node);
        }
        return;
      }
      const source = nodeSource(node);
      if (source) onNodeClick?.(source);
    });

    sigma.on('clickStage', clearSelection);

    sigma.getCamera().on('updated', () => {
      if (activeNodeId) updateTooltipPosition(sigma, activeNodeId);
    });
  }

  onMount(() => {
    const g = buildGraph(data);
    randomizePositions(g);
    graph = g;

    // Sigma throws immediately if its container has no width/height yet, which happens the
    // first time this mounts inside a layout that isn't already at final size (a panel still
    // animating open, a flex parent settling, a client-side route transition). Defer
    // construction until the container reports a real size instead of assuming onMount means
    // "laid out" — this also keeps the canvases in sync with the container on every later
    // resize (mobile rotation, a collapsing sidebar), which sigma does not watch on its own.
    const observer = new ResizeObserver(() => {
      if (renderer) {
        renderer.resize();
        return;
      }
      if (container.clientWidth === 0 || container.clientHeight === 0) return;

      const sigma: NoteSigma = new Sigma(g, container, { nodeReducer, edgeReducer });
      renderer = sigma;
      wireEvents(sigma);

      const graphLayout = createLayout(g);
      layout = graphLayout;
      graphLayout.start();
    });
    observer.observe(container);
    resizeObserver = observer;
  });

  onDestroy(() => {
    resizeObserver?.disconnect();
    layout?.kill();
    renderer?.kill();
  });
</script>

<div class="graph-view">
  <div class="graph-view__canvas" bind:this={container}></div>
  {#if activeSource}
    <div
      class="graph-view__tooltip"
      style:left="{tooltipPos?.x ?? 0}px"
      style:top="{tooltipPos?.y ?? 0}px"
    >
      <span class="graph-view__tooltip-title">{nodeLabel(activeSource)}</span>
      <span class="graph-view__tooltip-detail">{nodeDetail(activeSource)}</span>
    </div>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .graph-view {
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 240px;

    &__canvas {
      width: 100%;
      height: 100%;
    }

    &__tooltip {
      position: absolute;
      transform: translate(-50%, calc(-100% - 8px));
      pointer-events: none;
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: v.$space-xs v.$space-sm;
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      font-size: 0.8rem;
      white-space: nowrap;
      z-index: 10;
    }

    &__tooltip-title {
      color: v.$text-primary;
      font-weight: 600;
    }

    &__tooltip-detail {
      color: v.$text-secondary;
    }
  }
</style>
