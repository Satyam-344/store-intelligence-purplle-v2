"""
GET /stores/{store_id}/heatmap
Zone visit frequency + avg dwell, normalised 0–100.
data_confidence=false when fewer than 20 sessions in the window.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import check_db_health, get_db
from app.models import EventRecord, EventType, HeatmapResponse, HeatmapZone

log = structlog.get_logger()
router = APIRouter()

_ZONE_NAMES = {
    "ENTRANCE": "Entrance / Exit Threshold",
    "FOH": "Front of House",
    "BOH": "Back of House",
    "CASH_COUNTER": "Cash Counter / Billing",
    "WALL_LEFT": "Left Wall Units",
    "WALL_RIGHT": "Right Wall Units",
    "WALL_BACK": "Back Wall Units",
}


@router.get("/{store_id}/heatmap", response_model=HeatmapResponse)
def get_heatmap(store_id: str, db: Session = Depends(get_db)):
    if not check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "detail": "Database unavailable"},
        )

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    session_count = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.event_type == EventType.ENTRY.value,
            EventRecord.timestamp >= today_start,
        )
        .scalar() or 0
    )

    rows = (
        db.query(
            EventRecord.zone_id,
            func.count(EventRecord.id).label("visit_frequency"),
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
        )
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.zone_id.isnot(None),
            EventRecord.event_type.in_([
                EventType.ZONE_ENTER.value,
                EventType.ZONE_DWELL.value,
                EventType.ZONE_EXIT.value,
            ]),
            EventRecord.timestamp >= today_start,
        )
        .group_by(EventRecord.zone_id)
        .all()
    )

    if not rows:
        return HeatmapResponse(
            store_id=store_id,
            data_confidence=False,
            session_count=0,
            zones=[],
        )

    max_freq = max(r.visit_frequency for r in rows) or 1

    zones = [
        HeatmapZone(
            zone_id=row.zone_id,
            zone_name=_ZONE_NAMES.get(row.zone_id, row.zone_id),
            visit_frequency=int(row.visit_frequency),
            avg_dwell_ms=round(float(row.avg_dwell or 0), 2),
            normalised_score=round((row.visit_frequency / max_freq) * 100, 1),
        )
        for row in rows
    ]
    zones.sort(key=lambda z: z.normalised_score, reverse=True)

    return HeatmapResponse(
        store_id=store_id,
        data_confidence=session_count >= 20,
        session_count=session_count,
        zones=zones,
    )
