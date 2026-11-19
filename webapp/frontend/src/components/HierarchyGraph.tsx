// The IS_A graph: parents above, children below, laid out with dagre.
// Used for a concept's lineage (Explore) and for an answer's evidence (Ask).
import { memo, useEffect, useMemo } from "react";
import {
  Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";

export type GraphNode = {
  id: string; name: string; tag?: string | null;
  variant: "focus" | "ancestor" | "child" | "target" | "evidence" | "withheld" | "outside" | "more";
  note?: string;
};
export type GraphEdge = {
  child: string; parent: string;
  variant?: "plain" | "evidence" | "withheld" | "supported" | "memory" | "false" | "unknown" | "muted";
  label?: string;
};

const W = 196;
const H = 46;

const NODE_STYLE: Record<GraphNode["variant"], string> = {
  focus: "bg-ink text-white border-ink shadow-[0_6px_16px_-6px_rgba(20,33,61,.55)]",
  target: "bg-surface text-ink border-ink ring-1 ring-ink",
  ancestor: "bg-surface text-ink border-[#D3D9E2]",
  evidence: "bg-surface text-ink border-[#D3D9E2]",
  child: "bg-[#FAFBFC] text-ink-2 border-[#E1E5EB]",
  withheld: "bg-transparent text-ink-3 border-dashed border-ink-4",
  outside: "bg-wash text-ink-2 border-[#D3D9E2]",
  more: "bg-transparent text-ink-3 border-dashed border-[#C5CCD7] rounded-full",
};

const EDGE_STYLE: Record<NonNullable<GraphEdge["variant"]>, { stroke: string; dash?: string; width: number }> = {
  plain: { stroke: "#BCC4CF", width: 1.25 },
  muted: { stroke: "#D3D9E1", width: 1.25 },
  evidence: { stroke: "#9AA3B3", width: 1.4 },
  withheld: { stroke: "#B4BCC9", width: 1.4, dash: "2 5" },
  supported: { stroke: "#0E8A6A", width: 2.6 },
  memory: { stroke: "#B7791F", width: 2.6, dash: "7 5" },
  false: { stroke: "#C2413B", width: 2.6, dash: "7 5" },
  unknown: { stroke: "#6B4FBB", width: 2.6, dash: "7 5" },
};

type Data = GraphNode & { onOpen?: (id: string) => void };

const ConceptNode = memo(({ data }: NodeProps<Node<Data>>) => {
  const clickable = !!data.onOpen && data.variant !== "focus" && data.variant !== "more";
  return (
    <div
      className={`flex h-[46px] w-[196px] items-center rounded-lg border px-3 transition-[border-color,box-shadow] ${NODE_STYLE[data.variant]} ${clickable ? "cursor-pointer hover:border-ink/60 hover:shadow-node" : ""} ${data.variant === "more" ? "justify-center" : ""}`}
      title={data.variant === "more" ? data.name : `${data.name}${data.tag ? ` (${data.tag})` : ""}\n${data.id}${data.note ? `\n${data.note}` : ""}`}
    >
      <Handle type="target" position={Position.Top} />
      <span className={`line-clamp-2 text-[13px] leading-[16px] ${data.variant === "focus" ? "font-semibold" : "font-medium"}`}>{data.name}</span>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

const nodeTypes = { concept: ConceptNode };

function layout(nodes: GraphNode[], edges: GraphEdge[]) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 18, ranksep: 46, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: W, height: H }));
  edges.forEach((e) => g.setEdge(e.parent, e.child));
  dagre.layout(g);
  return new Map(nodes.map((n) => {
    const p = g.node(n.id);
    return [n.id, { x: p.x - W / 2, y: p.y - H / 2 }];
  }));
}

const BIG = 24;

function Inner({ nodes, edges, onOpen, height, fitKey, focusIds, scrollPans }: Props) {
  const flow = useReactFlow();
  const { rfNodes, rfEdges } = useMemo(() => {
    const ids = new Set(nodes.map((n) => n.id));
    const kept = edges.filter((e) => ids.has(e.child) && ids.has(e.parent));
    const pos = layout(nodes, kept);
    const rfNodes: Node<Data>[] = nodes.map((n) => ({
      id: n.id, type: "concept", position: pos.get(n.id)!, data: { ...n, onOpen },
      draggable: false, connectable: false, focusable: true, ariaLabel: n.name,
    }));
    const rfEdges: Edge[] = kept.map((e) => {
      const s = EDGE_STYLE[e.variant ?? "plain"];
      return {
        id: `${e.parent}-${e.child}-${e.variant ?? ""}`, source: e.parent, target: e.child, type: "default",
        label: e.label, labelStyle: { fontSize: 10.5, fontFamily: "IBM Plex Mono", fill: "#5E6982" },
        labelBgStyle: { fill: "#F3F5F7" }, labelBgPadding: [3, 2] as [number, number],
        style: { stroke: s.stroke, strokeWidth: s.width, strokeDasharray: s.dash },
        markerStart: ["plain", "muted"].includes(e.variant ?? "plain") ? undefined : { type: MarkerType.ArrowClosed, color: s.stroke, width: 13, height: 13 },
        selectable: false, focusable: false,
      };
    });
    return { rfNodes, rfEdges };
  }, [nodes, edges, onOpen]);

  // small graphs fit whole; big ones open on the focus neighbourhood (the corner button fits everything)
  useEffect(() => {
    const t = setTimeout(() => {
      const focus = focusIds?.[0] ? rfNodes.find((n) => n.id === focusIds[0]) : undefined;
      if (rfNodes.length > BIG && focus) {
        flow.setCenter(focus.position.x + W / 2, focus.position.y + H / 2 - 70, { zoom: 1, duration: 300 });
      } else {
        flow.fitView({ padding: 0.12, duration: 300, minZoom: 0.72, maxZoom: 1.1 });
      }
    }, 30);
    return () => clearTimeout(t);
  }, [fitKey, flow, rfNodes, focusIds]);

  return (
    <div style={{ height }} className="w-full">
      <ReactFlow
        nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} minZoom={0.15} maxZoom={1.6}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}
        onNodeClick={(_, n) => onOpen && n.id !== nodes.find((x) => x.variant === "focus")?.id && onOpen(n.id)}
        onNodeDoubleClick={(_, n) => onOpen?.(n.id)}
        panOnScroll={!!scrollPans} preventScrolling={!!scrollPans} zoomOnScroll={false} zoomOnPinch
      >
        <Controls showInteractive={false} position="top-right" fitViewOptions={{ padding: 0.08, duration: 300 }} />
      </ReactFlow>
    </div>
  );
}

type Props = {
  nodes: GraphNode[]; edges: GraphEdge[]; onOpen?: (id: string) => void; height: number | string; fitKey?: string;
  /** big graphs open zoomed on these nodes */
  focusIds?: string[];
  /** the scroll wheel pans the graph (only where the graph does not sit in the page's scroll path) */
  scrollPans?: boolean;
};

export default function HierarchyGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}
