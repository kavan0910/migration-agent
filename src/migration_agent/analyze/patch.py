"""Patch format emitted by chunk analysis and consumed by the merger (B-03/B-04).
Nodes are referenced by identity (type, module, qualified_symbol), never by ID; the registry
allocates IDs after merging so results do not depend on chunk completion order."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..graph.models import Evidence


class NodeRef(BaseModel):
    type: str
    module: str = ""
    qualified_symbol: str

    def key(self) -> tuple[str, str, str]:
        return (self.type, self.module, self.qualified_symbol)


class NodePatch(NodeRef):
    name: str
    responsibility: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    inventory_refs: list[str] = Field(default_factory=list)
    quirk: bool = False
    suspected_bug: bool = False


class EdgePatch(BaseModel):
    source: NodeRef
    target: NodeRef
    type: str
    evidence: list[Evidence] = Field(default_factory=list)


class UnresolvedRef(BaseModel):
    """A reference the chunk could not resolve (callee lives in another chunk)."""
    from_node: NodeRef
    name: str
    hint: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class ChunkPatch(BaseModel):
    chunk_id: str
    nodes: list[NodePatch] = Field(default_factory=list)
    edges: list[EdgePatch] = Field(default_factory=list)
    unresolved: list[UnresolvedRef] = Field(default_factory=list)
