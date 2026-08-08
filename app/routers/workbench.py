# app/routers/workbench.py
"""
Workbench endpoints — human-in-the-loop resolution of routed exceptions.

POST "" is the entry point that satisfies "1+ real exception routed to
Workbench" (calls services/triage.py). POST /{id}/resolve is the "resolved
by a human" half of that same requirement. Everything else here is
supporting CRUD/visibility, following the same shape as
routers/data_manager.py (list/summary/update) and routers/ai_policies.py
(the pattern this whole file is modeled on).
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.workbench import WorkbenchItem as WorkbenchItemModel
from ..models.workbench import WorkbenchStatus
from ..schemas.workbench import (
    EscalateWorkbenchItemRequest,
    ResolveWorkbenchItemRequest,
    RouteToWorkbenchRequest,
    WorkbenchItemResponse,
    WorkbenchItemUpdate,
    WorkbenchSummary,
)
from ..security import get_current_user
from ..services.audit import audit
from ..services.notifications import build_escalation_message, send_notification
from ..services.triage import route_to_workbench

log = logging.getLogger(__name__)

router = APIRouter(prefix="/workbench", tags=["Workbench"])


@router.get("", response_model=list[WorkbenchItemResponse])
def list_items(
    status: str | None = None,
    entity_name: str | None = None,
    db: Session = Depends(get_db),
):
    """List Workbench items, optionally filtered by status and/or
    entity_name — e.g. GET /api/workbench?status=pending"""
    query = db.query(WorkbenchItemModel)
    if status:
        query = query.filter(WorkbenchItemModel.status == status)
    if entity_name:
        query = query.filter(WorkbenchItemModel.entity_name == entity_name)
    return query.order_by(WorkbenchItemModel.created_at.desc()).all()


@router.get("/summary", response_model=WorkbenchSummary)
def get_summary(db: Session = Depends(get_db)):
    """Powers KPI cards — same computed-not-stored approach as
    routers/data_manager.py's get_summary()."""
    items = db.query(WorkbenchItemModel).all()
    counts = {"pending": 0, "in_progress": 0, "resolved": 0, "escalated": 0, "cancelled": 0}
    by_priority: dict[str, int] = {}
    for i in items:
        counts[i.status] = counts.get(i.status, 0) + 1
        by_priority[i.priority] = by_priority.get(i.priority, 0) + 1
    return WorkbenchSummary(
        total=len(items),
        pending=counts["pending"],
        in_progress=counts["in_progress"],
        resolved=counts["resolved"],
        escalated=counts["escalated"],
        cancelled=counts["cancelled"],
        by_priority=by_priority,
    )


@router.get("/{item_id}", response_model=WorkbenchItemResponse)
def get_item(item_id: str, db: Session = Depends(get_db)):
    item = db.query(WorkbenchItemModel).filter(WorkbenchItemModel.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Workbench item not found")
    return item


@router.post("", response_model=WorkbenchItemResponse)
async def create_item(
    body: RouteToWorkbenchRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Route a new exception to Workbench. This is the real entry point for
    "1+ real exception routed to Workbench" — call it from wherever a
    policy match or an Orchestrator step decides a human needs to weigh
    in, passing along whatever record/context a human needs to decide
    (payload) and why this needed a human at all (reason).
    """
    item = await route_to_workbench(
        db,
        title=body.title,
        description=body.description,
        entity_name=body.entity_name,
        entity_id=body.entity_id,
        source=body.source,
        payload=body.payload,
        reason=body.reason,
        priority=body.priority,
        notify_target=body.notify_target,
        max_retries=body.max_retries,
        retry_interval_minutes=body.retry_interval_minutes,
        actor=user,
    )
    return item


@router.patch("/{item_id}", response_model=WorkbenchItemResponse)
async def update_item(
    item_id: str,
    update: WorkbenchItemUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """General-purpose update — e.g. a human claiming an item
    (assigned_to) or moving it to in_progress before resolving it."""
    item = db.query(WorkbenchItemModel).filter(WorkbenchItemModel.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Workbench item not found")

    for field, value in update.dict(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)

    await audit.log(
        action="workbench.update",
        description=f"Updated Workbench item '{item.title}'",
        actor=user,
        category="data",
        resource_type="workbench_item",
        resource_id=item.id,
        resource_name=item.title,
        request=request,
    )
    return item


@router.post("/{item_id}/resolve", response_model=WorkbenchItemResponse)
async def resolve_item(
    item_id: str,
    body: ResolveWorkbenchItemRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    The "resolved by a human" half of the Round 2 requirement. Setting
    status to RESOLVED and clearing next_retry_at is what actually stops
    the scheduler from ever picking this item up again — see
    services/workbench_scheduler.py's query, which only looks at
    next_retry_at and status == pending/in_progress.
    """
    item = db.query(WorkbenchItemModel).filter(WorkbenchItemModel.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Workbench item not found")
    if item.status == WorkbenchStatus.RESOLVED:
        raise HTTPException(status_code=400, detail="Workbench item is already resolved")

    item.status = WorkbenchStatus.RESOLVED
    item.resolution = body.resolution
    item.resolved_by = (user or {}).get("email") or (user or {}).get("sub")
    item.resolved_at = datetime.now(timezone.utc)
    item.next_retry_at = None  # stop the retry/escalation clock — see docstring above
    db.commit()
    db.refresh(item)

    await audit.log(
        action="workbench.resolve",
        description=f"Workbench item '{item.title}' resolved: {body.resolution}",
        actor=user,
        category="data",
        resource_type="workbench_item",
        resource_id=item.id,
        resource_name=item.title,
        metadata={"actions_taken": body.actions_taken},
        request=request,
    )
    return item


@router.post("/{item_id}/escalate", response_model=WorkbenchItemResponse)
async def escalate_item(
    item_id: str,
    body: EscalateWorkbenchItemRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Manual escalation — a human deciding to hand this off, as opposed
    to services/workbench_scheduler.py's automatic escalation after
    max_retries is exhausted. Both paths converge on the same
    escalated/escalated_to/escalated_at fields."""
    item = db.query(WorkbenchItemModel).filter(WorkbenchItemModel.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Workbench item not found")

    now = datetime.now(timezone.utc)
    item.escalated = True
    item.escalated_to = body.escalated_to
    item.escalated_at = now
    item.status = WorkbenchStatus.ESCALATED
    item.next_retry_at = None  # manual escalation also stops the automatic retry clock
    db.commit()
    db.refresh(item)

    message = body.reason or build_escalation_message(item)
    await send_notification(item, message, body.escalated_to)

    await audit.log(
        action="workbench.escalate",
        description=f"Workbench item '{item.title}' manually escalated to {body.escalated_to}",
        actor=user,
        category="data",
        severity="warning",
        resource_type="workbench_item",
        resource_id=item.id,
        resource_name=item.title,
        metadata={"reason": body.reason},
        request=request,
    )
    return item
