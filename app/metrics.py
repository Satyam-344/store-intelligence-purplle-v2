"""
GET /stores/{store_id}/metrics
Real-time KPIs: unique visitors, conversion rate, avg dwell per zone, queue depth,
abandonment rate. Staff events excluded. Zero-purchase stores handled.
"""

from __future__ import annotations

from datetime import datetime, timezone, date

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import check_db_health, get_db
from app.models import EventRecord, EventType, MetricsResponse, POSTransaction, ZoneDwellStat

log = structlog.get_logger()
router = APIRouter()


@router.get("/{store_id}/metrics", response_model=MetricsResponse)
def get_metrics(store_id: str, db: Session = Depends(get_db)):
    if not check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "detail": "Database unavailable"},
        )

    today = date.today().isoformat()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    base_q = (
        db.query(EventRecord)
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.timestamp >= today_start,
        )
    )

    unique_visitors = (
        base_q.filter(EventRecord.event_type == EventType.ENTRY.value)
        .with_entities(func.count(func.distinct(EventRecord.visitor_id)))
        .scalar() or 0
    )

    billing_visitors = (
        base_q.filter(
            EventRecord.event_type.in_([
                EventType.BILLING_QUEUE_JOIN.value,
                EventType.ZONE_ENTER.value,
            ]),
            EventRecord.zone_id == "CASH_COUNTER",
        )
        .with_entities(func.count(func.distinct(EventRecord.visitor_id)))
        .scalar() or 0
    )

    conversion_rate = (billing_visitors / unique_visitors) if unique_visitors > 0 else 0.0

    dwell_rows = (
        base_q.filter(
            EventRecord.event_type.in_([EventType.ZONE_DWELL.value, EventType.ZONE_EXIT.value]),
            EventRecord.zone_id.isnot(None),
        )
        .with_entities(
            EventRecord.zone_id,
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
            func.count(EventRecord.id).label("visit_count"),
        )
        .group_by(EventRecord.zone_id)
        .all()
    )

    avg_dwell_per_zone = [
        ZoneDwellStat(
            zone_id=row.zone_id,
            avg_dwell_ms=round(float(row.avg_dwell or 0), 2),
            visit_count=int(row.visit_count),
        )
        for row in dwell_rows
    ]

    latest_queue = (
        base_q.filter(EventRecord.event_type == EventType.BILLING_QUEUE_JOIN.value)
        .order_by(EventRecord.timestamp.desc())
        .first()
    )
    current_queue_depth = 0
    if latest_queue and latest_queue.event_metadata:
        current_queue_depth = latest_queue.event_metadata.get("queue_depth") or 0

    queue_joins = (
        base_q.filter(EventRecord.event_type == EventType.BILLING_QUEUE_JOIN.value).count()
    )
    queue_abandons = (
        base_q.filter(EventRecord.event_type == EventType.BILLING_QUEUE_ABANDON.value).count()
    )
    abandonment_rate = (queue_abandons / queue_joins) if queue_joins > 0 else 0.0

    pos_q = db.query(POSTransaction).filter(
        POSTransaction.store_id == store_id,
        POSTransaction.timestamp >= today_start,
    )
    total_transactions = pos_q.count()
    avg_basket = pos_q.with_entities(func.avg(POSTransaction.basket_value_inr)).scalar()
    avg_basket_value = round(float(avg_basket), 2) if avg_basket else None

    return MetricsResponse(
        store_id=store_id,
        date=today,
        unique_visitors=unique_visitors,
        conversion_rate=round(conversion_rate, 4),
        avg_dwell_per_zone=avg_dwell_per_zone,
        current_queue_depth=current_queue_depth,
        abandonment_rate=round(abandonment_rate, 4),
        total_transactions=total_transactions,
        avg_basket_value_inr=avg_basket_value,
    )
