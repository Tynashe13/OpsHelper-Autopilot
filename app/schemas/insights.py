# app/schemas/insights.py
"""
Pydantic schemas for GET/POST /api/ai/insights — mirrors the frontend's
Insight / Pattern / ActionItem TypeScript interfaces
(frontend/src/components/ai/insights/*.tsx) field-for-field, the same
"no translation layer" convention models/policy.py uses for the Policy
Card components.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class InsightItem(BaseModel):
    id: str
    type: str  # 'pattern' | 'anomaly' | 'recommendation' | 'trend' | 'alert'
    severity: str  # 'critical' | 'high' | 'warning' | 'medium' | 'low' | 'info'
    title: str
    description: str
    data: Optional[dict[str, Any]] = None
    suggested_action: Optional[str] = None
    action_type: Optional[str] = None
    confidence: Optional[float] = None
    created_at: datetime
    is_dismissed: bool = False
    is_actioned: bool = False


class PatternItem(BaseModel):
    name: str
    frequency: str
    confidence: float
    sample_size: Optional[int] = None
    description: Optional[str] = None


class ActionItemSchema(BaseModel):
    title: str
    priority: str  # 'critical' | 'high' | 'medium' | 'low'
    estimated_impact: str
    action_type: Optional[str] = None
    action_config: Optional[dict[str, Any]] = None


class InsightsResponse(BaseModel):
    insights: list[InsightItem] = Field(default_factory=list)
    patterns: list[PatternItem] = Field(default_factory=list)
    actions: list[ActionItemSchema] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    based_on_records: int = 0
