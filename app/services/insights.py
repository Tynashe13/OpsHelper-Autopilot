# app/services/insights.py
"""
AI Insights engine — builds insights/patterns/actions straight from data
the system actually processed: PolicyEvaluation rows (every runtime
policy check — see services/policy_engine.py) and WorkbenchItem rows
(every exception routed to a human — see services/triage.py). This is
what satisfies the "AI Insights generated from data the agent actually
processed" checklist item; the frontend's DEMO_INSIGHTS/DEMO_PATTERNS/
DEMO_ACTIONS arrays it used to render are gone (see routers/insights.py
and frontend/src/app/ai/insights/page.tsx).

Deliberately deterministic aggregation, not an LLM call: every insight
here is a direct count/grouping over real rows, so it can't hallucinate
a pattern that isn't there, and — unlike routing this through Auto's
"LLM Judgment" workflow (services/auto_client.complete_json) — it keeps
working whether or not AUTO_API_KEY/AUTO_ORG_KEY are configured. There's
nothing to fail closed on. If richer natural-language insight text is
wanted later, that's a layer that could sit on top of these aggregates
(pass them to complete_json to phrase them), not a replacement for them.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.policy import PolicyEvaluation
from ..models.workbench import WorkbenchItem, WorkbenchStatus

log = logging.getLogger(__name__)


async def generate_insights(db: Session) -> dict[str, Any]:
    """
    Returns a dict matching schemas.insights.InsightsResponse's shape:
    {insights, patterns, actions, generated_at, based_on_records}.
    """
    insights: list[dict[str, Any]] = []
    patterns: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    total_evaluations = db.query(func.count(PolicyEvaluation.id)).scalar() or 0
    total_workbench = db.query(func.count(WorkbenchItem.id)).scalar() or 0
    based_on_records = total_evaluations + total_workbench

    # --- 1. Recurring policy pattern: which policies actually fire, and how often ---
    policy_rows = (
        db.query(PolicyEvaluation.policy_name, func.count(PolicyEvaluation.id))
        .filter(PolicyEvaluation.matched.is_(True))
        .group_by(PolicyEvaluation.policy_name)
        .order_by(func.count(PolicyEvaluation.id).desc())
        .all()
    )
    for policy_name, count in policy_rows:
        if count < 2:
            continue
        confidence = round(min(0.5 + count * 0.05, 0.97), 2)
        patterns.append(
            {
                "name": f"'{policy_name}' recurring match",
                "frequency": "ongoing",
                "confidence": confidence,
                "sample_size": count,
                "description": f"Policy '{policy_name}' matched {count} evaluated record(s) so far.",
            }
        )
        if count >= 3:
            insights.append(
                {
                    "id": f"policy-pattern-{policy_name}",
                    "type": "pattern",
                    "severity": "info",
                    "title": f"'{policy_name}' triggered {count} times",
                    "description": (
                        f"The policy '{policy_name}' has matched {count} records. "
                        "Recurring matches at this volume are worth a quick review to "
                        "confirm the rule is still calibrated correctly."
                    ),
                    "data": {"policy_name": policy_name, "match_count": count},
                    "suggested_action": None,
                    "action_type": None,
                    "confidence": confidence,
                    "created_at": now,
                }
            )

    # --- 2. Pending Workbench backlog ---
    pending_count = (
        db.query(func.count(WorkbenchItem.id))
        .filter(WorkbenchItem.status == WorkbenchStatus.PENDING)
        .scalar()
        or 0
    )
    if pending_count > 0:
        severity = "critical" if pending_count >= 8 else "warning" if pending_count >= 3 else "info"
        insights.append(
            {
                "id": "workbench-pending-backlog",
                "type": "trend",
                "severity": severity,
                "title": f"{pending_count} item(s) pending in Workbench",
                "description": (
                    f"There {'are' if pending_count != 1 else 'is'} currently "
                    f"{pending_count} unresolved exception(s) waiting for human review."
                ),
                "data": {"pending_count": pending_count},
                "suggested_action": "Review the Workbench queue",
                "action_type": "investigate",
                "confidence": 0.99,
                "created_at": now,
            }
        )
        actions.append(
            {
                "title": "Clear the Workbench backlog",
                "priority": "critical" if severity == "critical" else "high" if severity == "warning" else "medium",
                "estimated_impact": f"{pending_count} exception(s) awaiting a decision",
                "action_type": "investigate",
                "action_config": {"status": "pending"},
            }
        )

    # --- 3. Escalations — retries exhausted, handed to a different target ---
    escalated_count = (
        db.query(func.count(WorkbenchItem.id))
        .filter(WorkbenchItem.status == WorkbenchStatus.ESCALATED)
        .scalar()
        or 0
    )
    if escalated_count > 0:
        insights.append(
            {
                "id": "workbench-escalations",
                "type": "anomaly",
                "severity": "critical",
                "title": f"{escalated_count} item(s) escalated after exhausting retries",
                "description": (
                    f"{escalated_count} Workbench item(s) went unanswered through their "
                    "full retry schedule and were escalated to a different target."
                ),
                "data": {"escalated_count": escalated_count},
                "suggested_action": "Check whether the original notify_target is reachable",
                "action_type": "investigate",
                "confidence": 0.95,
                "created_at": now,
            }
        )

    # --- 4. Repeated resolutions -> a policy-creation opportunity ---
    # Grouping by `reason` (set by services/triage.py, usually naming the
    # policy/condition that routed the item) surfaces cases where a human
    # keeps making the same call manually — a candidate for a new policy.
    reason_rows = (
        db.query(WorkbenchItem.reason, func.count(WorkbenchItem.id))
        .filter(WorkbenchItem.status == WorkbenchStatus.RESOLVED, WorkbenchItem.reason.isnot(None))
        .group_by(WorkbenchItem.reason)
        .order_by(func.count(WorkbenchItem.id).desc())
        .all()
    )
    for reason, count in reason_rows:
        if count < 2:
            continue
        insights.append(
            {
                "id": f"repeat-resolution-{abs(hash(reason)) % 10_000_000}",
                "type": "recommendation",
                "severity": "info",
                "title": f"{count} human resolutions with the same reason",
                "description": (
                    f'"{reason}" was routed to a human and resolved {count} separate '
                    "times. If the resolution has been consistent, a policy could "
                    "cover this automatically."
                ),
                "data": {"reason": reason, "count": count},
                "suggested_action": "Consider creating a policy for this recurring case",
                "action_type": "create_policy",
                "confidence": round(min(0.5 + count * 0.08, 0.95), 2),
                "created_at": now,
            }
        )
        actions.append(
            {
                "title": f"Create a policy for: {reason[:60]}",
                "priority": "high" if count >= 4 else "medium",
                "estimated_impact": f"Removes {count} recurring manual review(s)",
                "action_type": "create_policy",
                "action_config": {"reason": reason, "observed_count": count},
            }
        )
        break  # surface only the single strongest recurring-reason opportunity

    return {
        "insights": insights,
        "patterns": patterns,
        "actions": actions,
        "generated_at": now,
        "based_on_records": based_on_records,
    }
