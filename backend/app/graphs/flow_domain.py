"""Logical attribute nodes share the existing entity graph and storage."""

from pydantic import BaseModel, Field

from app.graphs.domain import DependencyEdge

FLOW_EDGE_TYPES = frozenset(
    {
        "FLOWS_TO",
        "DERIVED_FROM",
        "PASSES_TO_MODULE_INPUT",
        "RETURNS_FROM_MODULE_OUTPUT",
        "PROPAGATES_THROUGH_LOCAL",
        "SINKS_TO_RESOURCE_ATTRIBUTE",
        "PASSES_TO_OUTPUT",
    }
)


class FlowSummary(BaseModel):
    flows: int = 0
    cross_module_flows: int = 0
    opaque_external_flows: int = 0
    unresolved_flows: int = 0


class DataFlowResult(BaseModel):
    edges: list[DependencyEdge] = Field(default_factory=list)
    diagnostics: list[dict] = Field(default_factory=list)
    summary: FlowSummary = Field(default_factory=FlowSummary)


def summarize_flows(edges: list[DependencyEdge]) -> FlowSummary:
    return FlowSummary(
        flows=len(edges),
        cross_module_flows=sum(bool(e.metadata.get("cross_module")) for e in edges),
        opaque_external_flows=sum(bool(e.metadata.get("opaque")) for e in edges),
        unresolved_flows=sum(not e.metadata.get("resolved", False) for e in edges),
    )
