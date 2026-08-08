# app/models/__init__.py
from .audit import AuditCategory, AuditLog, AuditSeverity
from .data_source import DataSource, DataSourceCategory, DataSourceStatus
from .item import Item
from .policy import Policy, PolicyEvaluation
from .settings import Settings
from .workbench import WorkbenchItem, WorkbenchStatus

# Imported via `from app.models import *` in alembic/env.py (autogenerate)
# and by scripts/reset_db.py's Base.metadata.create_all() — every model
# class needs to actually be imported HERE, not just exist as a file, or
# SQLAlchemy's metadata registry never learns the table exists and
# create_all()/autogenerate silently skips it. Policy/PolicyEvaluation/
# DataSource/WorkbenchItem were all missing from this file despite their
# tables existing via hand-written migrations — fixed here.
__all__ = [
    "Item",
    "Settings",
    "AuditLog",
    "AuditCategory",
    "AuditSeverity",
    "DataSource",
    "DataSourceCategory",
    "DataSourceStatus",
    "Policy",
    "PolicyEvaluation",
    "WorkbenchItem",
    "WorkbenchStatus",
]
