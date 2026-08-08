# app/schemas/ai_manager.py
"""
Pydantic schemas for the AI Manager surface — the "chat and orchestration
surface where a person asks the operation questions and watches the
Orchestrator delegate to Operators and report back" (problem statement's
own wording). This talks to the LIVE Supervity Auto orchestrator via
services/auto_client.py — it does not do any local LLM judgment itself.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AIManagerMessageRequest(BaseModel):
    message: str = Field(..., description="The operator's question or instruction")
    record: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional structured record to hand the orchestrator alongside the "
            "message (e.g. a specific disruption/exception to investigate). If "
            "omitted, only the free-text message is sent."
        ),
    )


class AIManagerActivityRun(BaseModel):
    id: Optional[str] = None
    step_id: Optional[str] = Field(default=None, alias="stepId")
    step_name: Optional[str] = Field(default=None, alias="stepName")
    status: Optional[str] = None
    outputs: Optional[dict[str, Any]] = None

    class Config:
        populate_by_name = True


class AIManagerMessageResponse(BaseModel):
    run_id: Optional[str] = None
    status: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    activity_runs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AIManagerErrorResponse(BaseModel):
    available: bool = False
    error: str
