"""Knowledge-graph models (A-08)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NodeType = Literal[
    "component", "endpoint", "data_entity", "external_dependency",
    "business_rule", "config", "job", "integration", "dead_code",
]
EdgeType = Literal["calls", "reads", "writes", "depends_on", "publishes", "consumes", "implements", "configures"]
Confidence = Literal["none", "low", "medium", "high"]

ID_PREFIX: dict[str, str] = {
    "component": "COMP", "endpoint": "EP", "data_entity": "ENT", "external_dependency": "EXT",
    "business_rule": "RULE", "config": "CFG", "job": "JOB", "integration": "INT", "dead_code": "DEAD",
}
EDGE_PREFIX = "EDGE"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    file: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    symbol: str | None = None
    quote: str | None = None

    @model_validator(mode="after")
    def _lines(self):
        if self.line_end is not None and self.line_start is None:
            raise ValueError("line_end requires line_start")
        if self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end < line_start")
        return self

    @property
    def strong(self) -> bool:
        """Pinpointed (lines or symbol) and backed by a verbatim quote."""
        return bool(self.quote and self.quote.strip()) and (self.line_start is not None or bool(self.symbol))


def derive_confidence(evidence: list[Evidence]) -> Confidence:
    if not evidence:
        return "none"
    strong = sum(e.strong for e in evidence)
    if strong >= 2:
        return "high"
    if strong == 1 or len(evidence) >= 2:
        return "medium"
    return "low"


class Node(_Strict):
    id: str
    type: NodeType
    name: str = Field(min_length=1)
    module: str = ""
    qualified_symbol: str = ""
    responsibility: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    inventory_refs: list[str] = Field(default_factory=list)
    quirk: bool = False
    suspected_bug: bool = False

    @model_validator(mode="after")
    def _id_prefix(self):
        prefix = ID_PREFIX[self.type]
        head, _, num = self.id.rpartition("-")
        if head != prefix or not num.isdigit():
            raise ValueError(f"node id {self.id!r} must look like {prefix}-NNNN for type {self.type}")
        return self

    @property
    def confidence(self) -> Confidence:
        return derive_confidence(self.evidence)


class Edge(_Strict):
    id: str
    source: str
    target: str
    type: EdgeType
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _id_prefix(self):
        head, _, num = self.id.rpartition("-")
        if head != EDGE_PREFIX or not num.isdigit():
            raise ValueError(f"edge id {self.id!r} must look like {EDGE_PREFIX}-NNNN")
        return self

    @property
    def confidence(self) -> Confidence:
        return derive_confidence(self.evidence)


class Graph(_Strict):
    version: int = Field(default=0, ge=0)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self):
        from collections import Counter
        ids = [n.id for n in self.nodes]
        known = set(ids)
        if len(known) != len(ids):  # O(n): Counter, not repeated list.count
            raise ValueError(f"duplicate node ids: {sorted(i for i, c in Counter(ids).items() if c > 1)}")
        eids = [e.id for e in self.edges]
        if len(set(eids)) != len(eids):
            raise ValueError(f"duplicate edge ids: {sorted(i for i, c in Counter(eids).items() if c > 1)}")
        for e in self.edges:
            for end in (e.source, e.target):
                if end not in known:
                    raise ValueError(f"edge {e.id} references unknown node {end}")
        return self

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)
