import type { components } from "@/api/generated/schema";
export type GraphNode = components["schemas"]["GraphNodeResponse"];
export type GraphEdge = components["schemas"]["GraphEdgeResponse"];
export type Neighborhood = components["schemas"]["GraphNeighborhoodResponse"];
export type Direction = Neighborhood["direction"];
export type FlowNode = components["schemas"]["FlowNode"];
export type FlowEdge = components["schemas"]["FlowEdgeResponse"];
export type VisualNode = {
  id: string;
  address: string;
  type: string;
  module_path?: string[];
  file_path?: string | null;
  line_start?: number | null;
  metadata?: Record<string, unknown>;
};
export type VisualEdge = {
  id: string;
  source: string;
  target: string | null;
  target_address?: string | null;
  type: string;
  expression?: string | null;
  metadata?: Record<string, unknown>;
};
