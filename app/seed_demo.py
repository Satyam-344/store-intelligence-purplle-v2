"""
Demo data seeder — generates realistic events for STORE_BLR_002 with today's timestamps.
Used by the HuggingFace Spaces deployment to pre-populate the database.
Only runs if the events table is empty.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog

log = structlog.get_logger()

STORE_ID = "STORE_BLR_002"
CAMERAS = {
    "entry": "CAM_ENTRY_01",
    "billing": "CAM_BILLING_01",
    "floor": "CAM_FLOOR_01",
}
ZONES = ["FOH", "WALL_LEFT", "WALL_RIGHT", "WALL_BACK", "CASH_COUNTER"]
SKU_MAP = {
    "FOH": "GENERAL",
    "WALL_LEFT": "SKINCARE",
    "WALL_RIGHT": "COSMETICS",
    "WALL_BACK": "HAIRCARE",
    "CASH_COUNTER": None,
}

# 42 visitors with their journey patterns (zone visits, billing %)
VISITOR_JOURNEYS = [
    # (visitor_suffix, zones_visited, reaches_billing, is_staff)
    ("v001", ["FOH", "WALL_LEFT", "WALL_RIGHT"], True, False),
    ("v002", ["FOH", "WALL_BACK"], True, False),
    ("v003", ["FOH", "WALL_LEFT"], False, False),
    ("v004", ["FOH", "WALL_RIGHT", "WALL_BACK"], True, False),
    ("v005", ["FOH"], False, False),
    ("v006", ["FOH", "WALL_LEFT", "WALL_BACK"], True, False),
    ("v007", ["FOH", "WALL_RIGHT"], True, False),
    ("v008", ["FOH"], False, False),
    ("v009", ["FOH", "WALL_LEFT"], True, False),
    ("v010", ["FOH", "WALL_BACK"], False, False),
    ("v011", ["FOH", "WALL_RIGHT"], True, False),
    ("v012", ["FOH", "WALL_LEFT", "WALL_RIGHT"], False, False),
    ("v013", ["FOH", "WALL_BACK"], True, False),
    ("v014", ["FOH"], False, False),
    ("v015", ["FOH", "WALL_LEFT"], True, False),
    ("v016", ["FOH", "WALL_RIGHT", "WALL_BACK"], True, False),
    ("v017", ["FOH", "WALL_LEFT"], False, False),
    ("v018", ["FOH", "WALL_BACK"], True, False),
    ("v019", ["FOH"], False, False),
    ("v020", ["FOH", "WALL_RIGHT"], True, False),
    ("v021", ["FOH", "WALL_LEFT", "WALL_BACK"], False, False),
    ("v022", ["FOH", "WALL_RIGHT"], True, False),
    ("v023", ["FOH"], False, False),
    ("v024", ["FOH", "WALL_LEFT"], True, False),
    ("v025", ["FOH", "WALL_BACK"], True, False),
    ("v026", ["FOH", "WALL_RIGHT"], False, False),
    ("v027", ["FOH", "WALL_LEFT"], True, False),
    ("v028", ["FOH"], False, False),
    ("v029", ["FOH", "WALL_BACK", "WALL_RIGHT"], True, False),
    ("v030", ["FOH", "WALL_LEFT"], True, False),
    # staff (excluded from metrics)
    ("s001", ["BOH", "FOH"], False, True),
    ("s002", ["BOH"], False, True),
    # re-entry visitor
    ("v031", ["FOH", "WALL_LEFT"], True, False),
    ("v032", ["FOH", "WALL_RIGHT"], False, False),
    ("v033", ["FOH", "WALL_BACK"], True, False),
    ("v034", ["FOH", "WALL_LEFT", "WALL_RIGHT"], True, False),
    ("v035", ["FOH"], False, False),
    ("v036", ["FOH", "WALL_BACK"], True, False),
    ("v037", ["FOH", "WALL_LEFT"], False, False),
    ("v038", ["FOH", "WALL_RIGHT"], True, False),
    ("v039", ["FOH"], False, False),
    ("v040", ["FOH", "WALL_LEFT", "WALL_BACK"], True, False),
]


def _ev(event_type: str, visitor_id: str, ts: datetime, camera_id: str,
        zone_id: str | None = None, dwell_ms: int = 0,
        is_staff: bool = False, metadata: dict | None = None) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.88,
        "event_metadata": metadata or {},
        "ingested_at": datetime.now(timezone.utc),
    }


def generate_demo_events() -> list[dict]:
    now = datetime.now(timezone.utc)
    today_open = now.replace(hour=10, minute=0, second=0, microsecond=0)

    events: list[dict] = []
    queue_depth = 0

    for i, (suffix, zones, reaches_billing, is_staff) in enumerate(VISITOR_JOURNEYS):
        visitor_id = f"VIS_{suffix}"
        entry_ts = today_open + timedelta(minutes=i * 14, seconds=i * 7)
        cam = CAMERAS["entry"]

        events.append(_ev("ENTRY", visitor_id, entry_ts, cam, is_staff=is_staff))

        t = entry_ts + timedelta(seconds=45)
        for seq, zone in enumerate(zones, start=1):
            z_cam = CAMERAS["billing"] if zone == "CASH_COUNTER" else CAMERAS["floor"]
            events.append(_ev("ZONE_ENTER", visitor_id, t, z_cam, zone_id=zone,
                               is_staff=is_staff,
                               metadata={"sku_zone": SKU_MAP.get(zone), "session_seq": seq}))
            dwell = 45_000 + seq * 8_000
            if dwell >= 30_000:
                events.append(_ev("ZONE_DWELL", visitor_id,
                                   t + timedelta(seconds=30), z_cam,
                                   zone_id=zone, dwell_ms=30_000,
                                   is_staff=is_staff,
                                   metadata={"sku_zone": SKU_MAP.get(zone), "session_seq": seq}))
            events.append(_ev("ZONE_EXIT", visitor_id, t + timedelta(milliseconds=dwell),
                               z_cam, zone_id=zone, dwell_ms=dwell, is_staff=is_staff,
                               metadata={"sku_zone": SKU_MAP.get(zone), "session_seq": seq}))
            t += timedelta(milliseconds=dwell + 15_000)

        if reaches_billing and not is_staff:
            queue_depth = (queue_depth % 6) + 1
            events.append(_ev("ZONE_ENTER", visitor_id, t,
                               CAMERAS["billing"], zone_id="CASH_COUNTER",
                               metadata={"queue_depth": queue_depth, "session_seq": len(zones) + 1}))
            events.append(_ev("BILLING_QUEUE_JOIN", visitor_id, t + timedelta(seconds=5),
                               CAMERAS["billing"], zone_id="CASH_COUNTER",
                               metadata={"queue_depth": queue_depth,
                                         "queue_position_at_join": queue_depth,
                                         "session_seq": len(zones) + 2}))
            # ~20% abandon
            if i % 5 == 0:
                events.append(_ev("BILLING_QUEUE_ABANDON", visitor_id,
                                   t + timedelta(seconds=120),
                                   CAMERAS["billing"], zone_id="CASH_COUNTER",
                                   metadata={"queue_depth": queue_depth}))

        exit_ts = t + timedelta(minutes=3)
        events.append(_ev("EXIT", visitor_id, exit_ts, CAMERAS["entry"], is_staff=is_staff))

    # one REENTRY
    reentry_id = "VIS_v001"
    reentry_ts = today_open + timedelta(hours=4, minutes=30)
    events.append(_ev("REENTRY", reentry_id, reentry_ts, CAMERAS["entry"]))
    events.append(_ev("ZONE_ENTER", reentry_id, reentry_ts + timedelta(minutes=1),
                       CAMERAS["floor"], zone_id="WALL_LEFT",
                       metadata={"session_seq": 1}))

    # spike event for anomaly demo (queue_depth=9 → CRITICAL)
    spike_ts = now - timedelta(minutes=5)
    spike_id = "VIS_spike01"
    events.append(_ev("ZONE_ENTER", spike_id, spike_ts,
                       CAMERAS["billing"], zone_id="CASH_COUNTER",
                       metadata={"queue_depth": 9, "session_seq": 1}))
    events.append(_ev("BILLING_QUEUE_JOIN", spike_id, spike_ts + timedelta(seconds=10),
                       CAMERAS["billing"], zone_id="CASH_COUNTER",
                       metadata={"queue_depth": 9, "queue_position_at_join": 9,
                                 "session_seq": 2}))

    events.sort(key=lambda e: e["timestamp"])
    return events


def seed_demo(db_session_factory) -> None:
    from app.models import EventRecord
    with db_session_factory() as db:
        count = db.query(EventRecord).count()
        if count > 0:
            log.info("demo_seed_skipped", existing_events=count)
            return

        events = generate_demo_events()
        records = [EventRecord(**e) for e in events]
        db.bulk_save_objects(records)
        db.commit()
        log.info("demo_seeded", event_count=len(records))
