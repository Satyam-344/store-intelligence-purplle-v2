"""
Event schema builder and HTTP emitter.
Builds canonical events matching Section 4 schema, batches them, and POSTs
to POST /events/ingest in batches of up to 500.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import structlog

log = structlog.get_logger()

API_BASE = "http://localhost:8000"
BATCH_SIZE = 500


class EventEmitter:
    def __init__(self, api_base: str = API_BASE, output_jsonl: Optional[str] = None):
        self.api_base = api_base.rstrip("/")
        self.buffer: list[dict] = []
        self.output_jsonl = output_jsonl
        self._jsonl_file = open(output_jsonl, "w", encoding="utf-8") if output_jsonl else None

    def emit(
        self,
        *,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        event_type: str,
        timestamp: datetime,
        zone_id: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        confidence: float,
        metadata: Optional[dict] = None,
    ) -> dict:
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(confidence, 4),
            "metadata": metadata or {"queue_depth": None, "sku_zone": None, "session_seq": 0},
        }
        self.buffer.append(event)

        if self._jsonl_file:
            self._jsonl_file.write(json.dumps(event) + "\n")
            self._jsonl_file.flush()

        if len(self.buffer) >= BATCH_SIZE:
            self.flush()

        return event

    def flush(self) -> int:
        if not self.buffer:
            return 0

        batch = self.buffer[:BATCH_SIZE]
        self.buffer = self.buffer[BATCH_SIZE:]

        try:
            resp = httpx.post(
                f"{self.api_base}/events/ingest",
                json={"events": batch},
                timeout=30.0,
            )
            resp.raise_for_status()
            result = resp.json()
            log.info(
                "batch_sent",
                ingested=result.get("ingested"),
                duplicates=result.get("duplicates"),
                failed=result.get("failed"),
                batch_size=len(batch),
            )
            return result.get("ingested", 0)
        except Exception as exc:
            log.error("batch_send_failed", error=str(exc), batch_size=len(batch))
            self.buffer = batch + self.buffer
            return 0

    def close(self):
        while self.buffer:
            self.flush()
        if self._jsonl_file:
            self._jsonl_file.close()


def build_metadata(
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
    session_seq: int = 0,
    group_id: Optional[str] = None,
    group_size: Optional[int] = None,
    zone_hotspot_x: Optional[float] = None,
    zone_hotspot_y: Optional[float] = None,
    queue_position_at_join: Optional[int] = None,
    wait_seconds: Optional[int] = None,
) -> dict:
    return {
        "queue_depth": queue_depth,
        "sku_zone": sku_zone,
        "session_seq": session_seq,
        "group_id": group_id,
        "group_size": group_size,
        "zone_hotspot_x": zone_hotspot_x,
        "zone_hotspot_y": zone_hotspot_y,
        "queue_position_at_join": queue_position_at_join,
        "wait_seconds": wait_seconds,
    }
