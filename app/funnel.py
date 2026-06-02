"""
GET /stores/{store_id}/funnel
Session-level conversion funnel: Entry → Zone Visit → Billing Queue → Purchase.
Re-entries are deduped — same visitor_id is not double-counted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import check_db_health, get_db
from app.models import EventRecord, EventType, FunnelResponse, FunnelStage, POSTransaction

log = structlog.get_logger()
router = APIRouter()


@router.get("/{store_id}/funnel", response_model=FunnelResponse)
def get_funnel(
    store_id: str,
    hours: int = Query(default=24, ge=1, le=168, description="Lookback window in hours"),
    db: Session = Depends(get_db),
):
    if not check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "detail": "Database unavailable"},
        )

    now = datetime.now(timezone.utc)
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def customer_events(event_types: list[str]) -> set[str]:
        rows = (
            db.query(EventRecord.visitor_id)
            .filter(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False,
                EventRecord.event_type.in_(event_types),
                EventRecord.timestamp >= window_start,
            )
            .distinct()
            .all()
        )
        return {r.visitor_id for r in rows}

    entered = customer_events([EventType.ENTRY.value])

    zone_visited = customer_events([
        EventType.ZONE_ENTER.value,
        EventType.ZONE_DWELL.value,
    ])

    billing_visited = (
        db.query(EventRecord.visitor_id)
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.zone_id == "CASH_COUNTER",
            EventRecord.event_type.in_([
                EventType.ZONE_ENTER.value,
                EventType.BILLING_QUEUE_JOIN.value,
            ]),
            EventRecord.timestamp >= window_start,
        )
        .distinct()
        .all()
    )
    billing_set = {r.visitor_id for r in billing_visited}

    entry_count = len(entered)
    zone_count = len(zone_visited & entered)
    billing_count = len(billing_set & entered)

    pos_count = (
        db.query(POSTransaction)
        .filter(
            POSTransaction.store_id == store_id,
            POSTransaction.timestamp >= window_start,
        )
        .count()
    )
    purchase_count = min(pos_count, billing_count)

    def drop_off(current: int, previous: int) -> float:
        if previous == 0:
            return 0.0
        return round((1 - current / previous) * 100, 2)

    stages = [
        FunnelStage(stage="ENTRY", visitor_count=entry_count, drop_off_pct=0.0),
        FunnelStage(stage="ZONE_VISIT", visitor_count=zone_count, drop_off_pct=drop_off(zone_count, entry_count)),
        FunnelStage(stage="BILLING_QUEUE", visitor_count=billing_count, drop_off_pct=drop_off(billing_count, zone_count)),
        FunnelStage(stage="PURCHASE", visitor_count=purchase_count, drop_off_pct=drop_off(purchase_count, billing_count)),
    ]

    return FunnelResponse(
        store_id=store_id,
        window_start=window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_end=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        stages=stages,
    )
