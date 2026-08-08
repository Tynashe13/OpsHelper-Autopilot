# app/routers/ai_manager.py
"""
AI Manager — the chat/orchestration surface the problem statement calls
for: "a chat and orchestration surface where a person asks the operation
questions and watches the Orchestrator delegate to Operators and report
back." This is a thin wrapper around services/auto_client.py, which talks
to the LIVE Supervity Auto orchestrator (already built + verified
directly on auto.supervity.ai, outside this codebase — see
AUTO_ORCHESTRATOR_WORKFLOW_ID in .env).

This router does NOT do any LLM judgment itself and does NOT duplicate
Policy Engine/Triage — it only triggers the orchestrator and relays back
what it did, so the frontend can render a live trace (which Operators ran,
what each returned).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.ai_manager import AIManagerMessageRequest, AIManagerMessageResponse
from ..security import get_current_user
from ..services.audit import audit
from ..services.auto_client import AutoOperatorError, trigger_orchestrator_run

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-manager", tags=["AI Manager"])


@router.post("/messages", response_model=AIManagerMessageResponse)
async def send_message(
    payload: AIManagerMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Sends a message (plus optional structured `record`) to the live Auto
    orchestrator and waits for its run to finish, returning the full
    per-step trace so the frontend can show "what the Orchestrator did" —
    not just a final answer.

    Raises 503 (not a 500) if the orchestrator is unreachable, misconfigured,
    or times out — this is a real external dependency that can be degraded
    independent of this app's own health, and the frontend should be able
    to tell the two apart.
    """
    record = dict(payload.record or {})
    record.setdefault("message", payload.message)

    try:
        result = await trigger_orchestrator_run(record=record, source="ai_manager")
    except RuntimeError as exc:
        # Missing env config — a setup problem, not a runtime failure.
        log.error("AI Manager: Auto orchestrator not configured: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AutoOperatorError as exc:
        log.error("AI Manager: Auto orchestrator call failed: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"Orchestrator unavailable: {exc}"
        ) from exc

    await audit.log(
        action="ai_manager.message_sent",
        actor=user,
        description=f"AI Manager message sent to Auto orchestrator (run {result.get('run_id')})",
        resource_type="ai_manager_run",
        resource_id=result.get("run_id"),
        metadata={"status": result.get("status"), "message": payload.message},
        request=request,
    )

    return AIManagerMessageResponse(
        run_id=result.get("run_id"),
        status=result.get("status") or "unknown",
        outputs=result.get("outputs") or {},
        activity_runs=result.get("activity_runs") or [],
    )
