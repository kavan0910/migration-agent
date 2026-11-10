"""Billing-agnostic seam (contract section 6).

No payment code, SDKs, keys or pricing live here. ``can_start_run`` is the only place plan limits are
enforced. ``BillingProvider`` is an interface; only ``NoBilling`` is shipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Entitlement, Membership, Plan, Project, Run, UsageEvent

DEFAULT_PLAN = "internal"
# Generous limits for the internal plan (resource ceilings, not prices). null/absent = unlimited.
DEFAULT_PLANS: dict[str, dict] = {
    "internal": {"max_projects": 1000, "max_members": 1000, "max_runs_per_month": 100000,
                 "max_concurrent_runs": 20, "max_usd_per_run": 1000.0, "monthly_usd_budget": 100000.0,
                 "max_source_mb": 500},
}
LIMIT_KEYS = ("max_projects", "max_members", "max_runs_per_month", "max_concurrent_runs", "max_usd_per_run",
              "monthly_usd_budget", "max_source_mb")


class BillingProvider(Protocol):
    def ensure_customer(self, workspace_id: str, name: str) -> str | None: ...
    def sync_plan(self, workspace_id: str, plan: str) -> None: ...
    def report_usage(self, workspace_id: str, usage: dict) -> None: ...


class NoBilling:
    def ensure_customer(self, workspace_id: str, name: str) -> str | None:
        return None

    def sync_plan(self, workspace_id: str, plan: str) -> None:
        return None

    def report_usage(self, workspace_id: str, usage: dict) -> None:
        return None


_provider: BillingProvider = NoBilling()


def get_billing() -> BillingProvider:
    return _provider


def set_billing(p: BillingProvider) -> None:
    global _provider
    _provider = p


def ensure_default_plans(db: Session) -> None:
    for name, limits in DEFAULT_PLANS.items():
        if db.get(Plan, name) is None:
            db.add(Plan(name=name, limits=limits))
    db.flush()


def ensure_entitlement(db: Session, workspace_id: str) -> Entitlement:
    ent = db.get(Entitlement, workspace_id)
    if ent is None:
        ensure_default_plans(db)
        ent = Entitlement(workspace_id=workspace_id, plan=DEFAULT_PLAN, overrides={})
        db.add(ent)
        db.flush()
    return ent


def effective_limits(db: Session, workspace_id: str) -> tuple[str, dict]:
    ent = ensure_entitlement(db, workspace_id)
    plan = db.get(Plan, ent.plan)
    limits = dict(plan.limits if plan else {})
    limits.update(ent.overrides or {})
    return ent.plan, limits


def month_start(at: datetime | None = None) -> datetime:
    at = at or datetime.now(timezone.utc)
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def consumption(db: Session, workspace_id: str) -> dict:
    ms = month_start()
    usd = db.scalar(select(func.coalesce(func.sum(UsageEvent.usd), 0.0))
                    .where(UsageEvent.workspace_id == workspace_id, UsageEvent.at >= ms)) or 0.0
    runs = db.scalar(select(func.count()).select_from(Run).where(Run.workspace_id == workspace_id, Run.created_at >= ms)) or 0
    active = db.scalar(select(func.count()).select_from(Run)
                       .where(Run.workspace_id == workspace_id, Run.status.in_(("queued", "running")))) or 0
    projects = db.scalar(select(func.count()).select_from(Project).where(Project.workspace_id == workspace_id)) or 0
    members = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)) or 0
    return {"usd_this_month": round(float(usd), 6), "runs_this_month": runs, "active_runs": active,
            "projects": projects, "members": members}


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    details: dict = field(default_factory=dict)


def can_start_run(db: Session, workspace_id: str, estimate: dict, max_usd: float | None = None) -> Decision:
    """The single enforcement point for plan limits. ``estimate`` is {"chunks", "usd"}."""
    plan, lim = effective_limits(db, workspace_id)
    c = consumption(db, workspace_id)
    est = float((estimate or {}).get("usd") or 0.0)
    budget = max_usd if max_usd is not None else est
    exposure = max(0.0, min(est, budget)) if max_usd is not None else est

    def lt(key: str, val: float, msg: str) -> Decision | None:
        cap = lim.get(key)
        return Decision(False, msg, {"limit": key, "cap": cap, "plan": plan}) if cap is not None and val > cap else None

    for d in (
        lt("max_usd_per_run", budget, "requested budget exceeds the per-run limit of the plan"),
        lt("max_concurrent_runs", c["active_runs"] + 1, "too many runs in progress for this workspace"),
        lt("max_runs_per_month", c["runs_this_month"] + 1, "monthly run limit reached"),
        lt("monthly_usd_budget", c["usd_this_month"] + exposure, "monthly spend limit would be exceeded"),
    ):
        if d:
            return d
    return Decision(True, "", {"plan": plan})


def remaining_monthly_usd(db: Session, workspace_id: str) -> float | None:
    _, lim = effective_limits(db, workspace_id)
    cap = lim.get("monthly_usd_budget")
    if cap is None:
        return None
    return max(0.0, float(cap) - consumption(db, workspace_id)["usd_this_month"])


def check_count_limit(db: Session, workspace_id: str, key: str, current: int) -> Decision:
    """Plan ceilings on counts (projects, members). Same data source as can_start_run."""
    plan, lim = effective_limits(db, workspace_id)
    cap = lim.get(key)
    if cap is not None and current + 1 > cap:
        return Decision(False, f"plan limit reached ({key})", {"limit": key, "cap": cap, "plan": plan})
    return Decision(True)
