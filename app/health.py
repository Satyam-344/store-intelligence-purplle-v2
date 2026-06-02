"""
GET /health — service health check.
Reports: db status, last event timestamp per store, STALE_FEED warnings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import check_db_health, get_db
from app.models import EventRecord, HealthResponse, StoreHealthStatus

log = structlog.get_logger()
router = APIRouter()

VERSION = "1.0.0"
STALE_FEED_THRESHOLD_MINUTES = 10


@router.get("/health", response_model=HealthResponse)
def get_health(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    db_ok = check_db_health()

    store_statuses: list[StoreHealthStatus] = []

    if db_ok:
        store_rows = (
            db.query(
                EventRecord.store_id,
                func.max(EventRecord.timestamp).label("last_ts"),
            )
            .group_by(EventRecord.store_id)
            .all()
        )

        for row in store_rows:
            last_ts = row.last_ts
            if last_ts is not None:
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                lag_s = (now - last_ts).total_seconds()
                stale = lag_s > STALE_FEED_THRESHOLD_MINUTES * 60
                status = "STALE_FEED" if stale else "OK"
                ts_str = last_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                lag_s = None
                ts_str = None
                status = "NO_DATA"

            store_statuses.append(StoreHealthStatus(
                store_id=row.store_id,
                last_event_timestamp=ts_str,
                lag_seconds=round(lag_s, 1) if lag_s is not None else None,
                status=status,
            ))

    overall = "ok" if db_ok else "degraded"
    db_status = "connected" if db_ok else "unavailable"

    return HealthResponse(
        status=overall,
        version=VERSION,
        db_status=db_status,
        stores=store_statuses,
        checked_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
