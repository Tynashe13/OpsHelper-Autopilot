# app/schemas/dashboard.py
"""
Pydantic schemas for GET /api/dashboard/summary. Field names/shapes match
frontend/src/app/page.tsx's TypeScript interfaces exactly (AgentStats,
DailyRunCount, RecentRun, SupabaseLiveCounts, DashboardSummary) — that
frontend file was given as a fixed target to build the backend around,
not the other way around, so every field here exists because the
frontend already expects it.

This frontend page was originally paired with a different backend
architecture (a `DisruptionRun`-per-event model). This app's real
architecture doesn't have that table — the mapping onto what actually
exists here (AuditLog + WorkbenchItem + PolicyEvaluation) is documented
field-by-field in routers/dashboard.py, not here; this file only defines
the response shape.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AgentStats(BaseModel):
    total: int
    pending_approval: int
    auto_executed: int
    approved: int
    rejected: int
    failed: int
    running: int


class DailyRunCount(BaseModel):
    date: str  # ISO date, e.g. "2026-08-13"
    count: int


class RecentRun(BaseModel):
    id: str
    supplier_label: str
    status: str  # 'running' | 'pending_approval' | 'auto_executed' | 'approved' | 'rejected' | 'failed'
    created_at: datetime
    cost_avoided: Optional[float] = None
    time_saved_hours: Optional[float] = None


class SupabaseLiveCounts(BaseModel):
    suppliers: Optional[int] = None
    disruption_notices: Optional[int] = None
    inventory_positions: Optional[int] = None
    shipments: Optional[int] = None
    configured: bool


class DashboardSummary(BaseModel):
    agent_stats: AgentStats
    cost_avoided_total: Optional[float] = None
    time_saved_hours_total: Optional[float] = None
    daily_run_counts: list[DailyRunCount]
    recent_runs: list[RecentRun]
    supabase_counts: SupabaseLiveCounts
