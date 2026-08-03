# app/schemas/data_source.py
"""
Pydantic schemas — these are DIFFERENT from the SQLAlchemy model in
app/models/data_source.py, even though the field names overlap heavily.
This is a common point of confusion, worth understanding:

  - The MODEL (models/data_source.py) describes the database table.
  - The SCHEMA (this file) describes the shape of data going in and out
    over the API — what a valid HTTP request body looks like, and what
    JSON the API promises to return.

FastAPI uses these schemas to auto-validate every request (reject it with
a clear 422 error before your code even runs, if a field is missing or the
wrong type) and to auto-generate the interactive API docs at /api/docs.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DataSourceBase(BaseModel):
    """Fields common to both creating and reading a data source. Other
    schemas below build on this with `class X(DataSourceBase):` instead of
    repeating every field — standard Pydantic inheritance pattern."""

    name: str
    category: str  # channel | system_of_record | human_loop | agent_platform
    system_type: str  # airtable | supabase | slack | email | auto | ...
    description: Optional[str] = None
    endpoint_url: Optional[str] = None
    config: Optional[str] = None  # JSON string, non-secret metadata only


class DataSourceCreate(DataSourceBase):
    """The shape of the JSON body for POST /api/data-manager/sources.
    Identical to DataSourceBase right now (hence `pass` — no extra fields),
    but kept as its own class so we can add create-only fields later
    without touching every other schema."""

    pass


class DataSourceUpdate(BaseModel):
    """The shape of the JSON body for PATCH /api/data-manager/sources/{id}.
    Every field is Optional here (unlike DataSourceBase) because a PATCH
    request should be allowed to update just one field — e.g. only
    `{"name": "New Name"}` — without being forced to resend everything."""

    name: Optional[str] = None
    category: Optional[str] = None
    system_type: Optional[str] = None
    description: Optional[str] = None
    endpoint_url: Optional[str] = None
    config: Optional[str] = None


class DataSource(DataSourceBase):
    """The shape of the JSON the API sends BACK to the frontend — includes
    everything in DataSourceBase plus the fields the database fills in
    itself (id, status, timestamps) that a client would never send us."""

    id: int
    status: str
    last_checked_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    latency_ms: Optional[float] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        # This is what lets FastAPI take a SQLAlchemy DataSource object
        # (from models/data_source.py) straight out of a database query and
        # convert it to this schema automatically, just by returning it
        # from a router function — no manual field-by-field copying needed.
        orm_mode = True


class DataSourceSummary(BaseModel):
    """Powers the Data Manager's KPI cards — this isn't a database table,
    it's a computed shape: counts derived by looping over every DataSource
    row (see get_summary() in routers/data_manager.py)."""

    total: int
    healthy: int
    degraded: int
    down: int
    unconfigured: int
    by_category: dict[str, int]  # e.g. {"channel": 2, "system_of_record": 1}
