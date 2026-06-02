# CHOICES.md — Three Key Engineering Decisions

---

## Decision 1 — Detection Model: YOLOv8n

### The Problem
I needed a person detector that could process 1080p, 15fps CCTV footage on CPU in
reasonable time (target: full 20-minute clip in under 30 minutes of processing).

### Options Considered

| Model | mAP50 (COCO) | CPU Latency (1080p) | Notes |
|-------|-------------|---------------------|-------|
| YOLOv8n | 37.3 | ~18ms/frame | Smallest YOLO v8 variant |
| YOLOv8m | 50.2 | ~55ms/frame | 3x slower |
| YOLOv9c | 53.0 | ~70ms/frame | Higher accuracy, much slower |
| RT-DETR-L | 53.0 | ~85ms/frame | Best accuracy, heaviest |
| MediaPipe | N/A | ~5ms/frame | Pose-based, misses partial views |

### What AI Suggested
RT-DETR-L was recommended for its transformer-based attention mechanism that handles
partial occlusion better than convolutional architectures. YOLOv9c was also noted as a
good middle ground.

### What I Chose and Why
**YOLOv8n.** At FRAME_SKIP=3 (process every 3rd frame from a 15fps source → 5 effective
fps), YOLOv8n takes ~18ms per processed frame, meaning the pipeline runs at roughly
300fps relative to wall clock — much faster than real time, which is what matters for
batch processing the 20-minute clips.

The 16-point mAP gap between YOLOv8n and RT-DETR matters in benchmark conditions. In
retail CCTV with relatively consistent backgrounds, YOLOv8n's accuracy is adequate. More
importantly, the confidence degradation strategy (emitting low-confidence events rather
than silently dropping them) partially compensates for missed detections: the API can
filter by confidence at query time without losing the audit trail.

**What would change my mind:** A GPU environment. With CUDA, RT-DETR-L runs at ~5ms/frame
and the accuracy difference becomes the deciding factor.

If you used a VLM for any part of the pipeline (e.g. zone classification, staff detection):
I evaluated using a Vision LLM for staff detection — prompting it to classify whether a
person in a bounding box ROI is staff or customer. The prompt was:
> "Look at this person in a retail store. They are either a staff member (wearing a
> uniform or apron) or a customer. Staff wear solid-colour uniforms, often dark blue
> or black. Is this person staff or customer? Answer with just 'staff' or 'customer'."

**Evaluation:** On 20 manual test ROIs from the entry clips, the VLM achieved 90% accuracy.
However, at ~500ms per inference (API latency + model), processing 5 people per frame at
5fps would take 12.5 seconds of VLM time per second of video — clearly infeasible.

**What I chose instead:** HSV uniform colour analysis (see `staff_detector.py`). It runs
in <1ms per ROI, achieves ~80% accuracy on these clips, and the staff uniform HSV range
can be configured per-store in `store_layout.json`.

---

## Decision 2 — Event Schema Design

### The Problem
I needed an event schema that supports 8 event types with different fields, handles
optional metadata (queue depth, group membership, demographic predictions), and is
validated at ingest time with partial success on malformed events.

### Options Considered

**Option A: Flat schema — all fields at top level**
```json
{"event_id": "...", "event_type": "ZONE_DWELL", "zone_id": "FOH",
 "dwell_ms": 8400, "queue_depth": null, "group_id": null, ...all 15 fields...}
```
Pro: Simple to query. Con: Sparse — most fields are null for most event types.

**Option B: Type-specific schemas — separate schema per event type**
Different Pydantic models per event type. Pro: Precise validation. Con: API complexity,
harder to ingest generically.

**Option C: Canonical schema + flexible metadata JSON (chosen)**
```json
{"event_id": "...", "event_type": "ZONE_DWELL",
 "zone_id": "FOH", "dwell_ms": 8400,
 "metadata": {"queue_depth": null, "sku_zone": "SKINCARE", "session_seq": 5}}
```
Core fields are always present and validated. Optional/event-type-specific data goes in
`metadata` (JSONB in PostgreSQL).

### What AI Suggested
Option B (type-specific schemas) was suggested for strictest validation at ingest.
Adding a `session_seq` field in metadata was also suggested to enable funnel ordering
without relying on timestamp microsecond precision.

### What I Chose and Why
**Option C.** The `metadata` JSONB field gives schema extensibility without API version
bumps. When the sample_events.jsonl revealed additional fields not in the problem statement
(`group_id`, `group_size`, `zone_hotspot_x`, `zone_hotspot_y`, `queue_position_at_join`),
I added them to `EventMetadata` without changing any core API contracts.

I agreed with the AI's `session_seq` suggestion and implemented it. It solved a real
problem: at FRAME_SKIP=3, two events in the same frame share the same timestamp, so
`session_seq` is the only reliable ordering key for within-session funnel analysis.

**Why not Kafka / a dedicated event stream:**
The problem statement says "Event Stream — you design this." For a single-store setup
with ~300 events/session, HTTP batch POST achieves sub-second latency with no operational
overhead. The API's WebSocket endpoint then pushes updates to the dashboard — completing
the "live stream" requirement without Kafka. The JSONL file output provides the same
durability that Kafka would offer.

---

## Decision 3 — API Architecture: Storage Engine Choice

### The Problem
Choose a storage engine for the intelligence API that: (a) supports idempotent ingest
via UNIQUE constraint, (b) runs inside Docker Compose with no external dependencies,
(c) handles time-range queries on event timestamps efficiently.

### Options Considered

| Engine | Pros | Cons |
|--------|------|------|
| SQLite | Zero setup, file-based, great for testing | No concurrent writes, no JSONB, no timezone-aware timestamps |
| PostgreSQL 15 | Full ACID, JSONB, native timezone support, `ON CONFLICT DO NOTHING` | Requires Docker service |
| TimescaleDB | Hypertable time-partitioning for large event volumes | Requires TimescaleDB extension, overkill for 40 stores |
| DynamoDB / MongoDB | Flexible schema | Not self-hosted, adds cloud dependency |

### What AI Suggested
TimescaleDB was suggested for its hypertable auto-partitioning by time, which would
make the 7-day anomaly lookback queries faster at scale. PostgreSQL was noted as
sufficient for 40 stores with several thousand events/day.

### What I Chose and Why
**PostgreSQL 15.** TimescaleDB is the right answer for a production system with millions
of events per day. For this challenge with 5 stores × 20-minute clips, PostgreSQL is
more than sufficient. The `(store_id, timestamp)` composite index covers all time-range
queries. TimescaleDB adds 200MB to the Docker image with no benefit at this scale.

I disagreed with the AI suggestion on TimescaleDB and chose standard PostgreSQL. The acceptance gate
requires `docker compose up` to work on a clean machine — simpler is better.

SQLite was used for tests (see `conftest.py`) — it avoids spinning up PostgreSQL in CI.
The test suite patches `check_db_health` to return True and overrides the database session,
so the ORM behaves identically in both environments.

**On idempotency implementation:**
PostgreSQL's `ON CONFLICT (event_id) DO NOTHING` is the correct primitive. I evaluated
an application-level `SELECT then INSERT` approach (checking for existence before insert)
but ruled it out: it creates a race condition under concurrent ingests. The database-level
unique constraint + `ON CONFLICT` is atomic and race-free.
