# DESIGN.md — Store Intelligence System Architecture

## Overview

This system converts raw CCTV footage from Apex Retail stores into real-time business
analytics. It is built as a 4-stage pipeline:

```
Raw CCTV Clips
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  Stage 1 · Detection Layer   (pipeline/)             │
│                                                      │
│  YOLOv8n → per-frame person detection                │
│  ByteTrack → multi-object tracking across frames     │
│  ResNet18 → appearance embeddings for Re-ID          │
│  HSV classifier → staff uniform detection            │
│  Polygon intersection → zone assignment              │
│  EventEmitter → batch HTTP POST to /events/ingest    │
└──────────────────────────┬──────────────────────────┘
                           │ POST /events/ingest (≤500/batch)
                           ▼
┌─────────────────────────────────────────────────────┐
│  Stage 2 · Event Stream   (JSONL + HTTP)             │
│                                                      │
│  Schema: event_id, store_id, camera_id, visitor_id,  │
│  event_type, timestamp, zone_id, dwell_ms, is_staff, │
│  confidence, metadata{}                              │
│  Batch size: 500 events, flush every ~10 seconds     │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│  Stage 3 · Intelligence API   (app/)                 │
│                                                      │
│  FastAPI + PostgreSQL 15                             │
│  POST /events/ingest   — idempotent, partial success │
│  GET  /stores/{id}/metrics  — real-time KPIs         │
│  GET  /stores/{id}/funnel   — session funnel         │
│  GET  /stores/{id}/heatmap  — zone visit density     │
│  GET  /stores/{id}/anomalies — active alerts         │
│  GET  /health               — STALE_FEED detection   │
│  WS   /ws/{store_id}        — live push stream       │
└──────────────────────────┬──────────────────────────┘
                           │ WebSocket
                           ▼
┌─────────────────────────────────────────────────────┐
│  Stage 4 · Live Dashboard   (dashboard/)             │
│                                                      │
│  React 18 + Vite + Recharts + TailwindCSS            │
│  WebSocket auto-reconnect on disconnect              │
│  Polled fallback every 5 seconds                     │
│  Components: MetricCard, FunnelChart, HeatmapGrid,   │
│              AnomalyFeed                             │
└─────────────────────────────────────────────────────┘
```

---

## Stage 1 — Detection Pipeline

### Person Detection: YOLOv8n

YOLOv8n was chosen over YOLOv9 and RT-DETR for its inference speed on CPU. At 15fps
source video with FRAME_SKIP=3, the effective rate is 5fps — YOLOv8n achieves 15–30ms
per frame on modern CPU hardware, keeping up in near-real-time.

The model uses COCO class 0 (person) only. Confidence threshold is set to 0.25 —
deliberately low to avoid silent drops on occluded or partially visible people.

### Multi-Object Tracking: ByteTrack

ByteTrack (via the `supervision` library) tracks individual bounding boxes across frames
using Kalman filter predictions + IoU matching. Unlike DeepSORT, ByteTrack does not
require a separate Re-ID model for within-clip tracking — it uses high and low confidence
association, which makes it more robust when people are occluded by display units or
other customers.

Group entry edge case: ByteTrack tracks each bounding box independently. If 3 people
enter together, 3 separate track IDs are created → 3 ENTRY events emitted. Group tagging
(`metadata.group_id`) is added post-hoc when 3+ ENTRY events occur within a 2-second
window with the same `entry_line_y` crossing.

### Re-ID: Appearance Embeddings

When a visitor exits and re-enters:
1. On EXIT: the track's appearance embedding (512-dim ResNet18 feature vector, normalised)
   is stored in `recent_exits` with a 30-minute TTL.
2. On new ENTRY detection at the threshold line: cosine similarity is computed against all
   embeddings in `recent_exits`.
3. If similarity > 0.85 within 30 minutes → emit REENTRY event, reuse existing visitor_id.
4. Otherwise: assign a new visitor_id.

Fallback when PyTorch is unavailable: 48-dim HSV histogram (colour distribution of torso ROI)
is used as the appearance embedding. This degrades gracefully — Re-ID still works but is
less accurate for people in similar-coloured clothing.

### Staff Classification

Staff are detected using HSV colour analysis of the torso region of each person's bounding
box:
- The upper 20%–70% of the bounding box height is treated as the torso.
- The dominant HSV values are computed.
- If the torso colour falls in the configured staff uniform HSV range (from `store_layout.json`),
  `is_staff=True` is set on all events for that track.

This avoids requiring a trained classifier. The HSV range can be adjusted per store by
updating `store_layout.json`.

### Zone Mapping

Each camera's zones are defined as pixel polygons in `store_layout.json`. A ray-casting
point-in-polygon algorithm tests whether a track's bounding box centroid falls within each
polygon. Zones are camera-specific — the billing camera's polygons differ from the floor
camera's polygons.

### Cross-Camera Deduplication

Two entry cameras (CAM_ENTRY_01 and CAM_ENTRY_02) overlap. To prevent double-counting:
- Both cameras share the same `ReIDTracker` instance (same recent_exits pool).
- If the same person appears in both within 30 seconds, cosine similarity > 0.85 triggers
  deduplication — the second sighting gets the same visitor_id.

---

## Stage 2 — Event Stream Design

Events are batched in memory and flushed as HTTP POST requests to `/events/ingest`.
Batch size: 500 events. Flush interval: triggered at 500 events or on clip completion.

**Why HTTP batches over Kafka/Redis Pub-Sub:**
For a take-home challenge targeting a single store's 20-minute clip, the added latency of
Kafka (requires ZooKeeper, broker setup, consumer group management) adds operational
complexity with no throughput benefit. The pipeline processes ~5 events/second; HTTP
batching handles this trivially. A Redis upgrade path is documented in CHOICES.md.

Events are also written to `data/output_events.jsonl` as a debug log. This is the audit
trail that allows replaying events into the API without re-running detection.

---

## Stage 3 — Intelligence API

### Database: PostgreSQL 15

- `events` table with UNIQUE constraint on `event_id` — the idempotency key.
- `ON CONFLICT (event_id) DO NOTHING` achieves idempotent ingest without locking.
- JSON metadata stored in a JSONB column for flexible schema extension.
- Composite indexes on `(store_id, timestamp)` and `(store_id, event_type)` support
  all analytics queries efficiently.

### Idempotency Pattern

`POST /events/ingest` adds each event to the DB session individually. On UNIQUE constraint
violation, it rolls back only that record and increments `duplicates`. The remaining events
in the batch are committed. This gives partial success without wrapping the entire batch
in a single transaction that fails all-or-nothing.

### Conversion Rate Logic

A visitor is "converted" if they have any ZONE_ENTER or BILLING_QUEUE_JOIN event at the
`CASH_COUNTER` zone. POS transactions are correlated by store and time window — not by
visitor_id (POS data has no customer identifier).

### Anomaly Detection

Three anomaly types:
1. **BILLING_QUEUE_SPIKE**: Latest queue_depth > 5 in the last 10 minutes.
   Severity escalates to CRITICAL above depth 8.
2. **CONVERSION_DROP**: Today's rate < 7-day rolling average − 2σ.
   Requires at least 3 historical days to fire (avoids false alarms on day 1).
3. **DEAD_ZONE**: No ZONE_ENTER in any product zone for 30+ minutes.
   Severity: INFO — operational notice rather than crisis.

---

## Stage 4 — Live Dashboard

The React dashboard connects to `/ws/{store_id}` via WebSocket. On every ingest call,
the API broadcasts a lightweight `LiveUpdate` payload (visitor count, queue depth,
conversion rate) to all connected clients.

The dashboard also polls the REST endpoints every 5 seconds as a fallback in case
WebSocket delivery is delayed. This ensures consistency: the WebSocket push triggers
an immediate refresh, but polling guarantees eventual consistency.

---

## AI-Assisted Decisions

### Decision 1 — Detection Model Selection
**What I asked:** "Compare YOLOv8n, YOLOv9, and RT-DETR for retail CCTV person detection
on CPU. I need to process 20-minute 1080p clips at 15fps with no GPU."

**What AI suggested:** RT-DETR (Real-Time DEtection TRansformer) for superior accuracy
on partially occluded people; also noted YOLOv9 has better mAP than YOLOv8n.

**What I chose and why I overrode:** YOLOv8n. RT-DETR requires ~4x more compute per
frame than YOLOv8n on CPU. At FRAME_SKIP=3 (processing every 3rd frame), YOLOv8n achieves
~5 effective fps which is sufficient for retail analytics. The 4% mAP difference between
RT-DETR and YOLOv8n matters less than the ability to actually run in reasonable time.
YOLOv9 was also ruled out for the same reason. If a GPU were available, RT-DETR would
be the first upgrade.

### Decision 2 — Event Schema `session_seq` Field
**What I asked:** "How should I track the ordinal position of events within a visitor's
session for funnel analysis?"

**What AI suggested:** Add a `session_seq` integer field in the event metadata — increment
per visitor_id per clip processing run. This allows the funnel endpoint to order events
within a session without relying on timestamp precision.

**What I chose:** Agreed and implemented. `session_seq` is tracked in `ReIDTracker`
per track_id and written to `metadata.session_seq`. This proved valuable for ordering
zone visits correctly when two events have the same timestamp (which happens at the
FRAME_SKIP=3 boundary).

### Decision 3 — Re-ID Similarity Threshold
**What I asked:** "What cosine similarity threshold should I use to identify re-entering
visitors? ResNet18 embeddings on 128x64 ROIs."

**What AI suggested:** 0.80 as a starting threshold, noting the trade-off: lower threshold
→ more re-entry detection but more false positives; higher threshold → misses real re-entries.

**What I chose:** 0.85. After manually inspecting the entry clips, I found that different
customers can have colour similarity scores of 0.76–0.82 (e.g. two people in similar
dark jeans). 0.85 keeps the false positive rate acceptable at the cost of occasionally
missing a real re-entry when lighting changes between exit and re-entry.
