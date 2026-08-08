# app/routers/insights.py
"""
AI Insights endpoints. GET returns a briefly-cached summary (this doesn't
need to be real-time — the underlying data changes on the order of
minutes, not seconds). POST /refresh forces a fresh generation; this is
what the frontend's "Generate Insights"/"Analyze" button calls.
"""
import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.insights import InsightsResponse
from ..security import get_current_user
from ..services.insights import generate_insights

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/insights", tags=["AI Insights"])

# Simple in-process cache — good enough for a single-instance deployment.
# Not shared across workers/replicas; if this app scales out horizontally,
# swap for a real cache (Redis) rather than assuming this survives that.
_CACHE_TTL_SECONDS = 120
_cache: dict = {"payload": None, "expires_at": 0.0}


@router.get("", response_model=InsightsResponse)
async def get_insights(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    now = time.monotonic()
    if _cache["payload"] is not None and now < _cache["expires_at"]:
        return _cache["payload"]

    result = await generate_insights(db)
    response = InsightsResponse(**result)
    _cache["payload"] = response
    _cache["expires_at"] = now + _CACHE_TTL_SECONDS
    return response


@router.post("/refresh", response_model=InsightsResponse)
async def refresh_insights(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    result = await generate_insights(db)
    response = InsightsResponse(**result)
    _cache["payload"] = response
    _cache["expires_at"] = time.monotonic() + _CACHE_TTL_SECONDS
    return response
