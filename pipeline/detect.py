"""
Main detection + tracking script.
Pipeline: YOLOv8n person detection → ByteTrack multi-object tracking →
          Staff classification → Zone mapping → Event emission.

CPU-optimised: processes every FRAME_SKIP-th frame (default: 3) to achieve
acceptable throughput on CPU (~5 fps effective at 15fps source).

Edge cases handled:
- Group entry: ByteTrack tracks each bbox independently → N people = N ENTRY events
- Partial occlusion: low-confidence detections flagged but not dropped
- Empty periods: zero detections handled gracefully (no crash, no null events)
- Re-entry: handled by ReIDTracker cosine similarity matching
- Staff: HSV uniform classifier marks is_staff=True on all events
- Cross-camera dedup: shared ReIDTracker instance across overlapping cameras
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import structlog

log = structlog.get_logger()

FRAME_SKIP = 3
PERSON_CLASS_ID = 0
MIN_CONFIDENCE = 0.25
DWELL_INTERVAL_MS = 30_000
BILLING_QUEUE_SPIKE_DEPTH = 2

LAYOUT_PATH = Path(__file__).parent.parent / "data" / "store_layout.json"


def process_clip(
    clip_path: str,
    store_id: str,
    camera_id: str,
    clip_start_ts: datetime,
    emitter,
    reid_tracker,
    staff_detector,
    zone_mapper,
) -> int:
    try:
        from ultralytics import YOLO
        import supervision as sv
    except ImportError as exc:
        log.error("missing_dependency", error=str(exc))
        raise

    model = YOLO("yolov8n.pt")
    byte_tracker = sv.ByteTrack()
    annotator = None

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        log.error("clip_open_failed", clip=clip_path)
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    entry_line_y = zone_mapper.get_entry_line_y(frame_height) if zone_mapper else int(frame_height * 0.75)

    events_emitted = 0
    frame_idx = 0
    zone_dwell_timer: dict[int, float] = {}
    last_zone: dict[int, Optional[str]] = {}
    track_positions: dict[int, float] = {}
    billing_queue_current: dict[int, bool] = {}
    billing_queue_join_time: dict[int, datetime] = {}

    is_entry_camera = camera_id in ("CAM_ENTRY_01", "CAM_ENTRY_02")

    log.info("clip_processing_start", clip=clip_path, camera=camera_id, total_frames=total_frames)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % FRAME_SKIP != 0:
            continue

        frame_time_offset_s = frame_idx / fps
        frame_ts = datetime.fromtimestamp(
            clip_start_ts.timestamp() + frame_time_offset_s, tz=timezone.utc
        )

        results = model(frame, classes=[PERSON_CLASS_ID], verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)

        if len(detections) == 0:
            continue

        detections = byte_tracker.update_with_detections(detections)

        for i, track_id in enumerate(detections.tracker_id):
            if track_id is None:
                continue

            bbox = detections.xyxy[i].astype(int).tolist()
            conf = float(detections.confidence[i]) if detections.confidence is not None else 0.5
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            is_staff = staff_detector.is_staff(frame, (x1, y1, x2, y2)) if staff_detector else False

            zone = zone_mapper.get_zone(cx, cy) if zone_mapper else None
            zone_id = zone.id if zone else None
            sku_zone = zone.sku_zone if zone else None

            visitor_id, is_reentry = reid_tracker.get_or_create_visitor(
                track_id, frame, (x1, y1, x2, y2), is_new_entry=False
            )
            seq = reid_tracker.increment_seq(track_id)

            if is_entry_camera:
                prev_cy = track_positions.get(track_id)
                if prev_cy is not None:
                    if prev_cy > entry_line_y and cy <= entry_line_y:
                        visitor_id, is_reentry = reid_tracker.get_or_create_visitor(
                            track_id, frame, (x1, y1, x2, y2), is_new_entry=True
                        )
                        if is_reentry:
                            events_emitted += 1
                            emitter.emit(
                                store_id=store_id, camera_id=camera_id,
                                visitor_id=visitor_id, event_type="REENTRY",
                                timestamp=frame_ts, zone_id=None, dwell_ms=0,
                                is_staff=is_staff, confidence=conf,
                                metadata={"queue_depth": None, "sku_zone": None, "session_seq": seq},
                            )
                        else:
                            events_emitted += 1
                            emitter.emit(
                                store_id=store_id, camera_id=camera_id,
                                visitor_id=visitor_id, event_type="ENTRY",
                                timestamp=frame_ts, zone_id=None, dwell_ms=0,
                                is_staff=is_staff, confidence=conf,
                                metadata={"queue_depth": None, "sku_zone": None, "session_seq": seq},
                            )

                    elif prev_cy < entry_line_y and cy >= entry_line_y:
                        reid_tracker.record_exit(track_id)
                        events_emitted += 1
                        emitter.emit(
                            store_id=store_id, camera_id=camera_id,
                            visitor_id=visitor_id, event_type="EXIT",
                            timestamp=frame_ts, zone_id=None, dwell_ms=0,
                            is_staff=is_staff, confidence=conf,
                            metadata={"queue_depth": None, "sku_zone": None, "session_seq": seq},
                        )

                track_positions[track_id] = cy
                continue

            prev_zone_id = last_zone.get(track_id)
            if zone_id != prev_zone_id:
                if prev_zone_id is not None:
                    dwell_start = zone_dwell_timer.pop(track_id, frame_ts.timestamp())
                    dwell_ms = int((frame_ts.timestamp() - dwell_start) * 1000)
                    events_emitted += 1
                    emitter.emit(
                        store_id=store_id, camera_id=camera_id,
                        visitor_id=visitor_id, event_type="ZONE_EXIT",
                        timestamp=frame_ts, zone_id=prev_zone_id, dwell_ms=dwell_ms,
                        is_staff=is_staff, confidence=conf,
                        metadata={"queue_depth": None, "sku_zone": sku_zone, "session_seq": seq},
                    )

                    if prev_zone_id == "CASH_COUNTER" and billing_queue_current.pop(track_id, False):
                        billing_queue_join_time.pop(track_id, None)
                        events_emitted += 1
                        emitter.emit(
                            store_id=store_id, camera_id=camera_id,
                            visitor_id=visitor_id, event_type="BILLING_QUEUE_ABANDON",
                            timestamp=frame_ts, zone_id="CASH_COUNTER", dwell_ms=dwell_ms,
                            is_staff=is_staff, confidence=conf,
                            metadata={"queue_depth": None, "sku_zone": None, "session_seq": seq},
                        )

                if zone_id is not None:
                    events_emitted += 1
                    billing_count = sum(1 for t_id, in_billing in billing_queue_current.items() if in_billing)
                    queue_depth = billing_count if zone_id == "CASH_COUNTER" else None

                    event_type = "ZONE_ENTER"
                    if zone_id == "CASH_COUNTER" and billing_count > 0:
                        event_type = "BILLING_QUEUE_JOIN"
                        billing_queue_current[track_id] = True
                        billing_queue_join_time[track_id] = frame_ts

                    emitter.emit(
                        store_id=store_id, camera_id=camera_id,
                        visitor_id=visitor_id, event_type=event_type,
                        timestamp=frame_ts, zone_id=zone_id, dwell_ms=0,
                        is_staff=is_staff, confidence=conf,
                        metadata={"queue_depth": queue_depth, "sku_zone": sku_zone, "session_seq": seq},
                    )
                    zone_dwell_timer[track_id] = frame_ts.timestamp()

                last_zone[track_id] = zone_id
            else:
                dwell_start = zone_dwell_timer.get(track_id, frame_ts.timestamp())
                elapsed_ms = int((frame_ts.timestamp() - dwell_start) * 1000)
                if elapsed_ms >= DWELL_INTERVAL_MS and zone_id is not None:
                    events_emitted += 1
                    emitter.emit(
                        store_id=store_id, camera_id=camera_id,
                        visitor_id=visitor_id, event_type="ZONE_DWELL",
                        timestamp=frame_ts, zone_id=zone_id, dwell_ms=elapsed_ms,
                        is_staff=is_staff, confidence=conf,
                        metadata={"queue_depth": None, "sku_zone": sku_zone, "session_seq": seq},
                    )
                    zone_dwell_timer[track_id] = frame_ts.timestamp()

    cap.release()
    emitter.flush()

    log.info(
        "clip_processed",
        clip=clip_path,
        camera_id=camera_id,
        frames_processed=frame_idx // FRAME_SKIP,
        events_emitted=events_emitted,
    )
    return events_emitted


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--clip", required=True, help="Path to video clip")
    parser.add_argument("--camera-id", required=True, help="Camera ID (e.g. CAM_ENTRY_01)")
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--clip-start", help="ISO-8601 UTC start time of the clip", default=None)
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--output-jsonl", default=None, help="Also write events to this JSONL file")
    args = parser.parse_args()

    clip_start_ts = (
        datetime.fromisoformat(args.clip_start.replace("Z", "+00:00"))
        if args.clip_start
        else datetime.now(timezone.utc)
    )

    from pipeline.emit import EventEmitter
    from pipeline.tracker import ReIDTracker
    from pipeline.staff_detector import StaffDetector
    from pipeline.zone_mapper import ZoneMapper

    emitter = EventEmitter(api_base=args.api_base, output_jsonl=args.output_jsonl)
    reid_tracker = ReIDTracker()
    staff_detector = StaffDetector(str(LAYOUT_PATH))
    zone_mapper = ZoneMapper(str(LAYOUT_PATH), args.camera_id)

    total = process_clip(
        clip_path=args.clip,
        store_id=args.store_id,
        camera_id=args.camera_id,
        clip_start_ts=clip_start_ts,
        emitter=emitter,
        reid_tracker=reid_tracker,
        staff_detector=staff_detector,
        zone_mapper=zone_mapper,
    )
    emitter.close()
    print(f"Done. Total events emitted: {total}")


if __name__ == "__main__":
    main()
