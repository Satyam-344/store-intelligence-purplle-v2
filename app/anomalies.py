"""
GET /stores/{store_id}/anomalies
Detects: BILLING_QUEUE_SPIKE, CONVERSION_DROP (vs 7-day avg), DEAD_ZONE.
Each anomaly has severity (INFO/WARN/CRITICAL) and suggested_action.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import check_db_health, get_db
from app.models import Anomaly, AnomaliesResponse, AnomalySeverity, EventRecord, EventType

log = structlog.get_logger()
router = APIRouter()

QUEUE_SPIKE_THRESHOLD = 5
QUEUE_SPIKE_MIN_DURATION_S = 180
DEAD_ZONE_MINUTES = 30
CONVERSION_DROP_SIGMA = 2.0


@router.get("/{store_id}/anomalies", response_model=AnomaliesResponse)
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    if not check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "detail": "Database unavailable"},
        )

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    anomalies: list[Anomaly] = []

    anomalies.extend(_check_queue_spike(db, store_id, now))
    anomalies.extend(_check_dead_zones(db, store_id, now, today_start))
    anomalies.extend(_check_conversion_drop(db, store_id, now, today_start))

    return AnomaliesResponse(
        store_id=store_id,
        active_anomalies=anomalies,
        checked_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _check_queue_spike(db: Session, store_id: str, now: datetime) -> list[Anomaly]:
    """Queue spike: depth > 5 in the last 10 minutes."""
    window = now - timedelta(minutes=10)
    recent_joins = (
        db.query(EventRecord)
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN.value,
            EventRecord.is_staff == False,
            EventRecord.timestamp >= window,
        )
        .order_by(EventRecord.timestamp.desc())
        .all()
    )

    if not recent_joins:
        return []

    max_depth = 0
    for event in recent_joins:
        depth = (event.event_metadata or {}).get("queue_depth") or 0
        if depth > max_depth:
            max_depth = depth

    if max_depth > QUEUE_SPIKE_THRESHOLD:
        severity = AnomalySeverity.CRITICAL if max_depth > 8 else AnomalySeverity.WARN
        log.warning("anomaly_detected", store_id=store_id, type="BILLING_QUEUE_SPIKE", depth=max_depth)
        return [Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity=severity,
            detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            description=f"Billing queue depth reached {max_depth} in the last 10 minutes.",
            suggested_action="Deploy additional billing staff immediately or open a secondary counter.",
            metadata={"max_queue_depth": max_depth, "window_minutes": 10},
        )]
    return []


def _check_dead_zones(
    db: Session, store_id: str, now: datetime, today_start: datetime
) -> list[Anomaly]:
    """Dead zone: no ZONE_ENTER in any zone for 30+ minutes during store hours."""
    # Only check dead zones if the store has been active today
    total_today = (
        db.query(func.count(EventRecord.id))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.timestamp >= today_start,
        )
        .scalar() or 0
    )
    if total_today == 0:
        return []

    threshold = now - timedelta(minutes=DEAD_ZONE_MINUTES)

    last_zone_events = (
        db.query(EventRecord.zone_id, func.max(EventRecord.timestamp).label("last_ts"))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.event_type == EventType.ZONE_ENTER.value,
            EventRecord.zone_id.isnot(None),
            EventRecord.timestamp >= today_start,
        )
        .group_by(EventRecord.zone_id)
        .all()
    )

    zone_ts_map = {row.zone_id: row.last_ts for row in last_zone_events}
    all_zones = {"FOH", "WALL_LEFT", "WALL_RIGHT", "WALL_BACK", "CASH_COUNTER"}

    dead_zones = []
    for zone in all_zones:
        last_ts = zone_ts_map.get(zone)
        if last_ts is None or (last_ts.tzinfo is None and last_ts.replace(tzinfo=timezone.utc) < threshold) or (last_ts.tzinfo is not None and last_ts < threshold):
            dead_zones.append(zone)

    anomalies = []
    for zone in dead_zones:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="DEAD_ZONE",
            severity=AnomalySeverity.INFO,
            detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            description=f"Zone {zone} has had no customer visits in the last {DEAD_ZONE_MINUTES} minutes.",
            suggested_action=f"Check if zone {zone} is accessible and products are well-displayed.",
            metadata={"zone_id": zone, "idle_minutes": DEAD_ZONE_MINUTES},
        ))
    return anomalies


def _check_conversion_drop(
    db: Session, store_id: str, now: datetime, today_start: datetime
) -> list[Anomaly]:
    """Conversion drop: today's rate < 7-day rolling average - 2σ."""
    day_rates = []
    for days_ago in range(1, 8):
        day_start = (today_start - timedelta(days=days_ago))
        day_end = day_start + timedelta(days=1)

        visitors = (
            db.query(func.count(func.distinct(EventRecord.visitor_id)))
            .filter(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False,
                EventRecord.event_type == EventType.ENTRY.value,
                EventRecord.timestamp >= day_start,
                EventRecord.timestamp < day_end,
            )
            .scalar() or 0
        )
        if visitors == 0:
            continue

        billing = (
            db.query(func.count(func.distinct(EventRecord.visitor_id)))
            .filter(
                EventRecord.store_id == store_id,
                EventRecord.is_staff == False,
                EventRecord.zone_id == "CASH_COUNTER",
                EventRecord.event_type.in_([
                    EventType.ZONE_ENTER.value,
                    EventType.BILLING_QUEUE_JOIN.value,
                ]),
                EventRecord.timestamp >= day_start,
                EventRecord.timestamp < day_end,
            )
            .scalar() or 0
        )
        day_rates.append(billing / visitors)

    if len(day_rates) < 3:
        return []

    today_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.event_type == EventType.ENTRY.value,
            EventRecord.timestamp >= today_start,
        )
        .scalar() or 0
    )

    if today_visitors == 0:
        return []

    today_billing = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.zone_id == "CASH_COUNTER",
            EventRecord.event_type.in_([
                EventType.ZONE_ENTER.value,
                EventType.BILLING_QUEUE_JOIN.value,
            ]),
            EventRecord.timestamp >= today_start,
        )
        .scalar() or 0
    )
    today_rate = today_billing / today_visitors

    avg = mean(day_rates)
    std = stdev(day_rates) if len(day_rates) > 1 else 0
    threshold = avg - CONVERSION_DROP_SIGMA * std

    if today_rate < threshold:
        log.warning(
            "anomaly_detected",
            store_id=store_id,
            type="CONVERSION_DROP",
            today_rate=today_rate,
            avg_rate=avg,
        )
        return [Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="CONVERSION_DROP",
            severity=AnomalySeverity.WARN,
            detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            description=(
                f"Today's conversion rate ({today_rate:.1%}) is significantly below "
                f"the 7-day average ({avg:.1%})."
            ),
            suggested_action=(
                "Review today's visitor journey in /funnel. Check for product availability, "
                "staff coverage, and queue abandonment rate."
            ),
            metadata={
                "today_rate": round(today_rate, 4),
                "seven_day_avg": round(avg, 4),
                "threshold": round(threshold, 4),
            },
        )]
    return []
