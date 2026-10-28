// Lineage's layout: a layered drawing of a directed graph, raw material at one end and outputs
// at the other, folders as clusters around their files. Computed by dagre in a Web Worker so a
// large lineage never stalls the page; the same function runs in the page where no worker can.
// A position is for finding one's way: nearness on the page is not evidence of anything.

import dagre from '@dagrejs/dagre';

export interface LayoutNode {
  id: string;
  width: number;
  height: number;
  cluster: string | null;
}

export interface LayoutEdge {
  id: string;
  from: string;
  to: string;
  /** Lines that order the layers; a mention orders too, faintly. */
  weight: number;
}

export interface LayoutRequest {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  clusters: Array<{id: string; label: string}>;
  /** 'TB': raw material at the top; 'BT': at the bottom; LR and RL across. */
  direction: 'TB' | 'BT' | 'LR' | 'RL';
}

export interface Placed {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LayoutResult {
  nodes: Record<string, Placed>;
  clusters: Record<string, Placed>;
  edges: Record<string, Array<{x: number; y: number}>>;
  width: number;
  height: number;
  milliseconds: number;
}

export function computeLayout(request: LayoutRequest): LayoutResult {
  const started = performance.now();
  const graph = new dagre.graphlib.Graph({compound: true, multigraph: true});
  graph.setGraph({
    rankdir: request.direction,
    nodesep: 22,
    ranksep: 54,
    edgesep: 12,
    marginx: 24,
    marginy: 24,
  });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const cluster of request.clusters) {
    graph.setNode(cluster.id, {label: cluster.label, clusterLabelPos: 'top', paddingTop: 22, paddingLeft: 10, paddingRight: 10, paddingBottom: 10});
  }
  for (const node of request.nodes) {
    graph.setNode(node.id, {width: node.width, height: node.height});
    if (node.cluster && graph.hasNode(node.cluster)) graph.setParent(node.id, node.cluster);
  }
  for (const edge of request.edges) {
    if (!graph.hasNode(edge.from) || !graph.hasNode(edge.to) || edge.from === edge.to) continue;
    graph.setEdge(edge.from, edge.to, {weight: edge.weight, minlen: 1}, edge.id);
  }
  dagre.layout(graph);
  const nodes: Record<string, Placed> = {};
  for (const node of request.nodes) {
    const placed = graph.node(node.id) as {x: number; y: number; width: number; height: number} | undefined;
    if (placed) nodes[node.id] = {x: placed.x, y: placed.y, width: node.width, height: node.height};
  }
  const clusters: Record<string, Placed> = {};
  for (const cluster of request.clusters) {
    const placed = graph.node(cluster.id) as {x: number; y: number; width: number; height: number} | undefined;
    if (placed && placed.width) clusters[cluster.id] = {x: placed.x, y: placed.y, width: placed.width, height: placed.height};
  }
  const edges: Record<string, Array<{x: number; y: number}>> = {};
  for (const handle of graph.edges()) {
    const held = graph.edge(handle) as {points?: Array<{x: number; y: number}>} | undefined;
    if (handle.name && held?.points) edges[handle.name] = held.points;
  }
  const size = graph.graph() as {width?: number; height?: number};
  return {
    nodes,
    clusters,
    edges,
    width: size.width ?? 0,
    height: size.height ?? 0,
    milliseconds: Math.round(performance.now() - started),
  };
}
