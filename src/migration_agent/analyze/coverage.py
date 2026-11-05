"""B-06 coverage checker: every inventory item must be mapped to a node or explicitly excluded."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..graph.models import Graph
from ..pipeline import Inventory, InventoryItem


class CoverageIncomplete(RuntimeError):
    def __init__(self, report: "CoverageReport"):
        super().__init__(f"coverage incomplete: {report.unmapped} unmapped inventory item(s): "
                         + ", ".join(i.id for i in report.unmapped_items[:10]))
        self.report = report


class CoverageReport(BaseModel):
    mapped: int
    unmapped: int
    excluded: int
    unmapped_items: list[InventoryItem] = Field(default_factory=list)
    excluded_items: dict[str, str] = Field(default_factory=dict)
    complete: bool


def check_coverage(inventory: Inventory, graph: Graph, excluded: dict[str, str] | None = None) -> CoverageReport:
    excluded = excluded or {}
    refs = {r for n in graph.nodes for r in n.inventory_refs}
    mapped = 0
    unmapped: list[InventoryItem] = []
    exc: dict[str, str] = {}
    for it in inventory.items:
        if it.id in refs:
            mapped += 1
        elif it.id in excluded:
            exc[it.id] = excluded[it.id]
        else:
            unmapped.append(it)
    return CoverageReport(mapped=mapped, unmapped=len(unmapped), excluded=len(exc), unmapped_items=unmapped,
                          excluded_items=exc, complete=not unmapped)


def assert_complete(report: CoverageReport) -> None:
    if report.unmapped > 0:
        raise CoverageIncomplete(report)
