"""
WebSocket endpoint /ws/{store_id} — push live metric updates to connected dashboard clients.
Broadcasts after every ingest call so dashboard sees real-time changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Set

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EventRecord, EventType, LiveUpdate

log = structlog.get_logger()
router = APIRouter()

_connections: Dict[str, Set[WebSocket]] = {}


@router.websocket("/ws/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await websocket.accept()
    _connections.setdefault(store_id, set()).add(websocket)
    log.info("ws_connected", store_id=store_id, total=len(_connections[store_id]))

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections[store_id].discard(websocket)
        log.info("ws_disconnected", store_id=store_id)


async def broadcast_update(store_id: str, db: Session) -> None:
    conns = _connections.get(store_id, set())
    if not conns:
        return

    payload = _build_live_update(store_id, db)
    msg = json.dumps(payload.model_dump())

    dead: Set[WebSocket] = set()
    for ws in list(conns):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)

    for ws in dead:
        conns.discard(ws)


def _build_live_update(store_id: str, db: Session) -> LiveUpdate:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    unique_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.event_type == EventType.ENTRY.value,
            EventRecord.timestamp >= today_start,
        )
        .scalar() or 0
    )

    billing_visitors = (
        db.query(func.count(func.distinct(EventRecord.visitor_id)))
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.zone_id == "CASH_COUNTER",
            EventRecord.timestamp >= today_start,
        )
        .scalar() or 0
    )

    conversion_rate = (billing_visitors / unique_visitors) if unique_visitors > 0 else 0.0

    latest_queue = (
        db.query(EventRecord)
        .filter(
            EventRecord.store_id == store_id,
            EventRecord.event_type == EventType.BILLING_QUEUE_JOIN.value,
        )
        .order_by(EventRecord.timestamp.desc())
        .first()
    )
    queue_depth = 0
    if latest_queue and latest_queue.event_metadata:
        queue_depth = latest_queue.event_metadata.get("queue_depth") or 0

    return LiveUpdate(
        store_id=store_id,
        event_type="METRICS_UPDATE",
        unique_visitors=unique_visitors,
        current_queue_depth=queue_depth,
        conversion_rate=round(conversion_rate, 4),
        timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
