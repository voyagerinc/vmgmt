"""Policy & rule engine (PRD §20).

A rule is JSON:
    {
      "when": {"type": "health|software|activity|file|device"},
      "if":   [{"field": "cpu_percent", "op": ">", "value": 90, "for_seconds": 600}, ...],
      "then": [{"action": "alert", "severity": "high", "message": "..."},
               {"action": "screenshot"}, {"action": "notify"}, {"action": "block"},
               {"action": "ticket"}, {"action": "log"}]
    }
Scope: tenant -> site -> department -> group -> device, resolved by priority (lower = evaluated first).
"""
from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import Device, Policy

_OPS = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
    "contains": lambda a, b: b.lower() in str(a).lower(),
    "in": lambda a, b: a in b,
    "matches": lambda a, b: str(b).lower() in str(a).lower(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_effective(policy: Policy) -> bool:
    if not policy.enabled:
        return False
    now = _now()
    if policy.effective_from:
        ef = policy.effective_from
        if (ef.replace(tzinfo=timezone.utc) if ef.tzinfo is None else ef) > now:
            return False
    if policy.effective_to:
        et = policy.effective_to
        if (et.replace(tzinfo=timezone.utc) if et.tzinfo is None else et) < now:
            return False
    return True


def applies_to_device(policy: Policy, device: Device) -> bool:
    st, sv = policy.scope_type, policy.scope_value
    if st == "tenant" or not sv:
        return True
    if st == "department":
        return (device.department or "") == sv
    if st == "location" or st == "site":
        return (device.location or "") == sv
    if st == "device":
        return device.id == sv or device.machine_uid == sv
    return False


def _within_exceptions(policy: Policy, device: Device) -> bool:
    for exc in policy.exceptions or []:
        if isinstance(exc, dict):
            if exc.get("device_id") == device.id:
                return True
            if exc.get("department") and exc["department"] == device.department:
                return True
    return False


def _eval_condition(cond: dict[str, Any], sample: dict[str, Any], state: dict) -> bool:
    field = cond.get("field")
    op = cond.get("op", "==")
    want = cond.get("value")
    have = sample.get(field)
    if have is None:
        return False
    fn = _OPS.get(op)
    if not fn:
        return False
    try:
        ok = fn(have, want)
    except Exception:
        return False
    # sustained-duration handling (e.g. CPU > 90% for 600s)
    for_seconds = cond.get("for_seconds")
    if ok and for_seconds:
        key = f"{field}:{op}:{want}"
        first = state.setdefault("_breach", {}).get(key)
        now = _now().timestamp()
        if first is None:
            state["_breach"][key] = now
            return False
        return (now - first) >= for_seconds
    elif not ok:
        state.get("_breach", {}).pop(f"{field}:{op}:{want}", None)
    return ok


def matching_policies(db: Session, tenant_id: str, when_type: str, device: Device) -> list[Policy]:
    rows = (
        db.query(Policy)
        .filter(Policy.tenant_id == tenant_id)
        .order_by(Policy.priority.asc())
        .all()
    )
    out = []
    for p in rows:
        if not is_effective(p):
            continue
        if (p.rule or {}).get("when", {}).get("type") != when_type:
            continue
        if not applies_to_device(p, device):
            continue
        if _within_exceptions(p, device):
            continue
        out.append(p)
    return out


def evaluate(policy: Policy, sample: dict[str, Any], device_state: dict) -> list[dict]:
    """Return the list of THEN actions if all IF conditions pass, else []."""
    conds = policy.rule.get("if", [])
    if conds and not all(_eval_condition(c, sample, device_state) for c in conds):
        return []
    return policy.rule.get("then", [{"action": "log"}])
