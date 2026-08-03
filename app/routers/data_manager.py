# app/routers/data_manager.py
"""
Data Manager endpoints — the live registry of every connected system and
its health, required by the Round 2 Operations problem statement.

Register a DataSource row for each of your integrations (the channel, the
system of record, the human loop, and the Auto platform itself). The
Command Center's Data Manager page polls GET /summary and GET "" to show
live status; POST /{id}/check runs a real reachability check and logs the
result to the audit trail so every health evaluation is auditable.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.database import get_db
# Imported with an alias (`as DataSourceModel`) because the schema file
# ALSO exports something called `DataSource` — without the alias, the
# second import below would silently overwrite this one. This is a common
# FastAPI-project convention: model imports get a `Model` suffix so model
# and schema of the same name can coexist in one file.
from ..models.data_source import DataSource as DataSourceModel
from ..schemas.data_source import (
    DataSource,
    DataSourceCreate,
    DataSourceSummary,
    DataSourceUpdate,
)
from ..security import get_current_user
from ..services.audit import audit
from ..services.health_check import check_data_source

log = logging.getLogger(__name__)

# APIRouter groups related endpoints together. `prefix="/data-manager"`
# means every route below is automatically prefixed — @router.get("/sources")
# actually becomes GET /data-manager/sources, and main.py adds one more
# "/api" on top of that when it mounts this router. `tags` just controls
# how these routes are grouped in the auto-generated /api/docs page.
router = APIRouter(prefix="/data-manager", tags=["Data Manager"])

# ---------------------------------------------------------------------------
# Common pattern used in every function below — worth understanding once:
#
#   `db: Session = Depends(get_db)` — FastAPI's dependency injection. Before
#   your function runs, FastAPI calls get_db() to open a database
#   connection, hands it to you as `db`, and closes it automatically after
#   your function returns (even if it raised an error). You never manually
#   open/close a connection anywhere in this file.
#
#   `user: dict = Depends(get_current_user)` — same idea, but for "who is
#   making this request." Reads the request's auth token (or, in
#   AUTH_BYPASS dev mode, returns a fake dev user) and hands you the result.
#
#   `response_model=X` on the decorator — tells FastAPI "convert whatever
#   this function returns into this schema shape before sending it back,"
#   and also tells the auto-generated docs what the response looks like.
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=list[DataSource])
def list_sources(category: str | None = None, db: Session = Depends(get_db)):
    """
    List all connected systems, optionally filtered by category.
    `category: str | None = None` becomes an optional URL query parameter
    automatically — FastAPI infers this from the type hint, e.g.
    GET /api/data-manager/sources?category=channel
    """
    query = db.query(DataSourceModel)
    if category:
        query = query.filter(DataSourceModel.category == category)
    # .all() actually runs the SQL query and returns real Python objects —
    # everything above this line just builds up the query without running it.
    return query.order_by(DataSourceModel.category, DataSourceModel.name).all()


@router.get("/summary", response_model=DataSourceSummary)
def get_summary(db: Session = Depends(get_db)):
    """
    Powers the Data Manager's KPI cards. This is computed on every request
    by looping over all rows in Python — fine at this table's size (a
    handful of connected systems), would need a real SQL GROUP BY if this
    table ever grew to thousands of rows.
    """
    sources = db.query(DataSourceModel).all()
    by_category: dict[str, int] = {}
    counts = {"healthy": 0, "degraded": 0, "down": 0, "unconfigured": 0}
    for s in sources:
        # dict.get(key, 0) returns 0 if the key isn't there yet instead of
        # raising a KeyError — this is the standard Python "counting" idiom.
        counts[s.status] = counts.get(s.status, 0) + 1
        by_category[s.category] = by_category.get(s.category, 0) + 1
    return DataSourceSummary(
        total=len(sources),
        healthy=counts["healthy"],
        degraded=counts["degraded"],
        down=counts["down"],
        unconfigured=counts["unconfigured"],
        by_category=by_category,
    )


@router.post("/sources", response_model=DataSource)
async def create_source(
    source: DataSourceCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Register a new connected system."""
    # `source` arrives already validated against the DataSourceCreate
    # schema (FastAPI does this automatically before this function even
    # runs — an invalid request never reaches this line). `source.dict()`
    # turns it into a plain Python dict, and `**` unpacks that dict into
    # keyword arguments, so this line is equivalent to writing
    # DataSourceModel(name=source.name, category=source.category, ...) by
    # hand for every field — just shorter and stays correct if fields change.
    db_source = DataSourceModel(**source.dict())
    db.add(db_source)      # stages the new row (not saved yet)
    db.commit()             # actually writes it to Postgres
    db.refresh(db_source)   # pulls back any DB-generated values (id, created_at)

    # Every create/update/delete in this router writes an audit log entry —
    # this is what satisfies "every action logged" for the Data Manager,
    # the same pattern the Policy Engine reuses for "every evaluation logged".
    await audit.log(
        action="data_source.create",
        description=f"Registered data source '{db_source.name}' ({db_source.system_type})",
        actor=user,
        category="data",
        resource_type="data_source",
        resource_id=str(db_source.id),
        resource_name=db_source.name,
        request=request,
    )
    return db_source


@router.patch("/sources/{source_id}", response_model=DataSource)
async def update_source(
    source_id: int,
    update: DataSourceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    db_source = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    if db_source is None:
        # 404 here becomes a real HTTP 404 response — FastAPI catches this
        # exception type specifically and turns it into the right status
        # code + JSON error body automatically.
        raise HTTPException(status_code=404, detail="Data source not found")

    # exclude_unset=True is the key to making PATCH work correctly: it
    # returns ONLY the fields the client actually included in the request
    # body, skipping any field left at its default (None). Without this,
    # sending {"name": "New Name"} would wipe out every other field back to
    # None, since DataSourceUpdate's other fields default to None too.
    for field, value in update.dict(exclude_unset=True).items():
        # setattr(obj, "name", value) is the same as obj.name = value, but
        # lets us do it in a loop with a variable field name instead of
        # writing one line per possible field.
        setattr(db_source, field, value)
    db.commit()
    db.refresh(db_source)

    await audit.log(
        action="data_source.update",
        description=f"Updated data source '{db_source.name}'",
        actor=user,
        category="data",
        resource_type="data_source",
        resource_id=str(db_source.id),
        resource_name=db_source.name,
        request=request,
    )
    return db_source


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    db_source = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    if db_source is None:
        raise HTTPException(status_code=404, detail="Data source not found")

    name = db_source.name
    db.delete(db_source)
    db.commit()

    await audit.log(
        action="data_source.delete",
        description=f"Removed data source '{name}'",
        actor=user,
        category="data",
        resource_type="data_source",
        resource_id=str(source_id),
        request=request,
    )
    return {"message": "Data source deleted"}


@router.post("/sources/{source_id}/check", response_model=DataSource)
async def check_source(
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Run a live reachability check against one connected system and
    persist + audit-log the result. This is what the Data Manager page's
    'Test Connection' button calls."""
    db_source = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    if db_source is None:
        raise HTTPException(status_code=404, detail="Data source not found")

    # This is the actual "go check if it's alive right now" call — it hands
    # off to services/health_check.py, which picks the right checker
    # function based on system_type and does the real network request.
    # This router function doesn't know or care HOW the check happens,
    # only what to do with the result — that separation is on purpose.
    status, latency_ms, error_message = await check_data_source(
        db_source.system_type, db_source.endpoint_url, db_source.config
    )

    db_source.status = status
    db_source.latency_ms = latency_ms
    db_source.error_message = error_message
    db_source.last_checked_at = datetime.now(timezone.utc)
    if status == "healthy":
        db_source.last_success_at = db_source.last_checked_at
    db.commit()
    db.refresh(db_source)

    await audit.log(
        action="data_source.health_check",
        description=f"Health check for '{db_source.name}': {status}",
        actor=user,
        category="data",
        severity="warning" if status in ("degraded", "down") else "info",
        resource_type="data_source",
        resource_id=str(db_source.id),
        resource_name=db_source.name,
        metadata={"status": status, "latency_ms": latency_ms, "error": error_message},
        success=status in ("healthy", "degraded"),
        error_message=error_message,
        request=request,
    )
    return db_source


@router.post("/sources/check-all")
async def check_all_sources(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Refresh health for every registered system in one call — handy for
    a page-load refresh or a scheduled poll."""
    sources = db.query(DataSourceModel).all()
    results = []
    for db_source in sources:
        status, latency_ms, error_message = await check_data_source(
            db_source.system_type, db_source.endpoint_url, db_source.config
        )
        db_source.status = status
        db_source.latency_ms = latency_ms
        db_source.error_message = error_message
        db_source.last_checked_at = datetime.now(timezone.utc)
        if status == "healthy":
            db_source.last_success_at = db_source.last_checked_at
        results.append({"id": db_source.id, "name": db_source.name, "status": status})
    db.commit()

    await audit.log(
        action="data_source.health_check_all",
        description=f"Bulk health check across {len(sources)} data sources",
        actor=user,
        category="data",
        metadata={"results": results},
        request=request,
    )
    return {"checked": len(sources), "results": results}
