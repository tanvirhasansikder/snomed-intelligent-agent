// The IS_A graph: parents above, children below, laid out with dagre.
// Used for a concept's lineage (Explore) and for an answer's evidence (Ask).
import { memo, useEffect, useMemo } from "react";
import {
  Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";

export type GraphNode = {
  id: string; name: string; tag?: string | null;
  variant: "focus" | "ancestor" | "child" | "target" | "evidence" | "withheld" | "outside";
  note?: string;
};
export type GraphEdge = {
  child: string; parent: string;
  variant?: "plain" | "evidence" | "withheld" | "supported" | "memory" | "false" | "unknown" | "muted";
  label?: string;
};

const W = 216;
const H = 66;

const NODE_STYLE: Record<GraphNode["variant"], string> = {
  focus: "bg-ink text-white border-ink",
  target: "bg-surface text-ink border-ink border-2",
  ancestor: "bg-surface text-ink border-rule-strong",
  evidence: "bg-surface text-ink border-rule-strong",
  child: "bg-paper text-ink-2 border-rule border-dashed",
  withheld: "bg-paper text-ink-3 border-rule border-dashed",
  outside: "bg-wash text-ink-3 border-rule",
};

const EDGE_STYLE: Record<NonNullable<GraphEdge["variant"]>, { stroke: string; dash?: string; width: number }> = {
  plain: { stroke: "#B4BCC9", width: 1.4 },
  muted: { stroke: "#CBD1DB", width: 1.2, dash: "4 4" },
  evidence: { stroke: "#8D97A9", width: 1.5 },
  withheld: { stroke: "#B4BCC9", width: 1.4, dash: "2 5" },
  supported: { stroke: "#0E8A6A", width: 2.6 },
  memory: { stroke: "#B7791F", width: 2.6, dash: "7 5" },
  false: { stroke: "#C2413B", width: 2.6, dash: "7 5" },
  unknown: { stroke: "#6B4FBB", width: 2.6, dash: "7 5" },
};

type Data = GraphNode & { onOpen?: (id: string) => void };

const ConceptNode = memo(({ data }: NodeProps<Node<Data>>) => {
  const clickable = !!data.onOpen && data.variant !== "focus";
  return (
    <div
      className={`group flex h-[66px] w-[216px] flex-col justify-center rounded-lg border px-3 py-2 shadow-node transition-colors ${NODE_STYLE[data.variant]} ${clickable ? "cursor-pointer hover:border-ink" : ""}`}
      title={data.note ? `${data.name}: ${data.note}` : `${data.name}${data.tag ? ` (${data.tag})` : ""}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="line-clamp-2 text-[13px] font-medium leading-[17px]">{data.name}</div>
      <div className={`mt-0.5 truncate font-mono text-[10.5px] ${data.variant === "focus" ? "text-white/65" : "text-ink-4"}`}>
        {data.id}{data.tag ? ` ${data.tag}` : ""}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

const nodeTypes = { concept: ConceptNode };

function layout(nodes: GraphNode[], edges: GraphEdge[]) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 26, ranksep: 54, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: W, height: H }));
  edges.forEach((e) => g.setEdge(e.parent, e.child));
  dagre.layout(g);
  return new Map(nodes.map((n) => {
    const p = g.node(n.id);
    return [n.id, { x: p.x - W / 2, y: p.y - H / 2 }];
  }));
}

function Inner({ nodes, edges, onOpen, height, fitKey }: Props) {
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
        id: `${e.parent}-${e.child}-${e.variant ?? ""}`, source: e.parent, target: e.child, type: "smoothstep",
        label: e.label, labelStyle: { fontSize: 10.5, fontFamily: "IBM Plex Mono", fill: "#5E6982" },
        labelBgStyle: { fill: "#F3F5F7" }, labelBgPadding: [3, 2] as [number, number],
        style: { stroke: s.stroke, strokeWidth: s.width, strokeDasharray: s.dash },
        markerStart: { type: MarkerType.ArrowClosed, color: s.stroke, width: 14, height: 14 },
        selectable: false, focusable: false,
      };
    });
    return { rfNodes, rfEdges };
  }, [nodes, edges, onOpen]);

  useEffect(() => {
    const t = setTimeout(() => flow.fitView({ padding: 0.12, duration: 300, maxZoom: 1.05 }), 30);
    return () => clearTimeout(t);
  }, [fitKey, flow, rfNodes.length]);

  return (
    <div style={{ height }} className="w-full">
      <ReactFlow
        nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView minZoom={0.15} maxZoom={1.6}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} proOptions={{ hideAttribution: true }}
        onNodeClick={(_, n) => onOpen && n.id !== nodes.find((x) => x.variant === "focus")?.id && onOpen(n.id)}
        onNodeDoubleClick={(_, n) => onOpen?.(n.id)}
        panOnScroll zoomOnScroll={false} zoomOnPinch
      >
        <Background gap={22} size={1} color="#D6DCE4" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

type Props = {
  nodes: GraphNode[]; edges: GraphEdge[]; onOpen?: (id: string) => void; height: number | string; fitKey?: string;
};

export default function HierarchyGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}
