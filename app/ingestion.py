"""
POST /events/ingest — batch event ingestion with idempotency and partial success.
Idempotent by event_id (UNIQUE constraint in DB, ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import check_db_health, get_db
from app.models import EventCreate, EventRecord, IngestError, IngestRequest, IngestResponse
from app.websocket import broadcast_update

log = structlog.get_logger()
router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(payload: IngestRequest, db: Session = Depends(get_db)):
    if not check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"error": "SERVICE_UNAVAILABLE", "detail": "Database is unavailable"},
        )

    ingested = 0
    duplicates = 0
    errors: list[IngestError] = []
    store_ids_seen: set[str] = set()

    for event in payload.events:
        try:
            record = _to_orm(event)
            db.add(record)
            db.flush()
            ingested += 1
            store_ids_seen.add(event.store_id)
        except Exception as exc:
            db.rollback()
            err_str = str(exc)
            if "uq_event_id" in err_str or "UNIQUE CONSTRAINT" in err_str.upper():
                duplicates += 1
                log.debug("duplicate_event_skipped", event_id=event.event_id)
            else:
                errors.append(IngestError(event_id=event.event_id, error=_clean_error(err_str)))
                log.warning("event_ingest_failed", event_id=event.event_id, error=err_str)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        err_str = str(exc)
        if "uq_event_id" in err_str or "UNIQUE constraint" in err_str.upper():
            duplicates += ingested
            ingested = 0
        else:
            raise HTTPException(
                status_code=503,
                detail={"error": "SERVICE_UNAVAILABLE", "detail": "Failed to commit events"},
            )

    log.info(
        "events_ingested",
        total_received=len(payload.events),
        ingested=ingested,
        duplicates=duplicates,
        failed=len(errors),
    )

    for store_id in store_ids_seen:
        await broadcast_update(store_id, db)

    return IngestResponse(
        ingested=ingested,
        duplicates=duplicates,
        failed=len(errors),
        errors=errors,
    )


def _to_orm(event: EventCreate) -> EventRecord:
    ts_str = event.timestamp.replace("Z", "+00:00")
    ts = datetime.fromisoformat(ts_str)
    meta = event.metadata.model_dump() if event.metadata else {}

    return EventRecord(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=event.visitor_id,
        event_type=event.event_type.value,
        timestamp=ts,
        zone_id=event.zone_id,
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        event_metadata=meta,
        ingested_at=datetime.now(timezone.utc),
    )


def _clean_error(err: str) -> str:
    """Strip internal DB details from error messages — never expose stack traces."""
    for keyword in ["DETAIL:", "CONTEXT:", "HINT:", "LOCATION:"]:
        if keyword in err:
            err = err[: err.index(keyword)].strip()
    return err[:200]
