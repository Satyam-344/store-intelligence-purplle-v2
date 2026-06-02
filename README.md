# Store Intelligence System

End-to-end retail analytics from raw CCTV footage.
Built for the Purplle Engineering Hiring Challenge (PS3f02573).

**Pipeline:** YOLOv8n → ByteTrack → Re-ID → FastAPI + PostgreSQL → React Dashboard

---

## Quick Start (5 commands)

```bash
# 1. Clone and enter the project
git clone <repo-url> store-intelligence && cd store-intelligence

# 2. Add your CCTV clips and POS data to the project root
#    (billing_area.mp4, entry 1.mp4, entry 2.mp4, zone.mp4, data/pos_transactions.csv)

# 3. Start the API, database, and dashboard
docker compose up

# 4. (In a new terminal) Run the detection pipeline on all 4 clips
API_BASE=http://localhost:8000 bash pipeline/run.sh

# 5. Open the live dashboard
open http://localhost:3000
```

The API docs are available at http://localhost:8000/docs.

---

## Architecture

```
CCTV Clips → Detection Pipeline → POST /events/ingest → PostgreSQL
                                                              ↓
Dashboard ← WebSocket ← FastAPI API ← /metrics /funnel /heatmap /anomalies
```

Full architecture details: [docs/DESIGN.md](docs/DESIGN.md)

---

## Running the Detection Pipeline

The pipeline processes each CCTV clip independently and posts events to the API:

```bash
# Process all clips (requires API to be running)
bash pipeline/run.sh

# Or process a single clip
python -m pipeline.detect \
  --clip "entry 1.mp4" \
  --camera-id CAM_ENTRY_01 \
  --store-id STORE_BLR_002 \
  --clip-start 2026-03-03T10:00:00Z \
  --api-base http://localhost:8000 \
  --output-jsonl data/output_events.jsonl
```

Events are written to `data/output_events.jsonl` for debugging and audit purposes.

**CPU performance:** Processing a 20-minute 1080p clip takes approximately 8–15 minutes
on a modern CPU (processes every 3rd frame for speed).

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Batch ingest ≤500 events (idempotent by event_id) |
| GET | `/stores/{id}/metrics` | Today's KPIs: visitors, conversion, queue depth |
| GET | `/stores/{id}/funnel` | Session-level conversion funnel with drop-off % |
| GET | `/stores/{id}/heatmap` | Zone visit frequency + dwell, normalised 0–100 |
| GET | `/stores/{id}/anomalies` | Active anomalies with severity and suggested action |
| GET | `/health` | Service health, STALE_FEED warnings per store |
| WS | `/ws/{store_id}` | WebSocket live updates for dashboard |

### Example: Get Store Metrics
```bash
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```
```json
{
  "store_id": "STORE_BLR_002",
  "date": "2026-03-03",
  "unique_visitors": 42,
  "conversion_rate": 0.357,
  "avg_dwell_per_zone": [...],
  "current_queue_depth": 2,
  "abandonment_rate": 0.12,
  "total_transactions": 15
}
```

### Example: Ingest Events
```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events": [{
    "event_id": "550e8400-e29b-41d4-a716-446655440000",
    "store_id": "STORE_BLR_002",
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": "VIS_abc123",
    "event_type": "ENTRY",
    "timestamp": "2026-03-03T10:15:00Z",
    "zone_id": null,
    "dwell_ms": 0,
    "is_staff": false,
    "confidence": 0.92,
    "metadata": {"queue_depth": null, "sku_zone": null, "session_seq": 1}
  }]}'
```

---

## Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests with coverage
pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=70

# Run specific test class
pytest tests/test_metrics.py::TestMetricsEmptyStore -v
```

Tests use an in-memory SQLite database — no PostgreSQL required for testing.

---

## Development Setup

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt --extra-index-url https://download.pytorch.org/whl/cpu

# Start PostgreSQL only (for local API dev)
docker compose up db

# Run the API locally
DATABASE_URL=postgresql://store_intel:store_intel@localhost:5432/store_intel \
  uvicorn app.main:app --reload --port 8000

# Run the dashboard locally
cd dashboard && npm install && npm run dev
```

---

## Live Dashboard

Open **http://localhost:3000** after starting `docker compose up`.

The dashboard shows:
- **Live visitor count** (updates on every event ingest via WebSocket)
- **Conversion rate** (billing zone visitors / total visitors)
- **Queue depth gauge** (highlights red when > 5)
- **Abandonment rate**
- **Conversion funnel** bar chart (Entry → Zone Visit → Billing → Purchase)
- **Zone heatmap** (normalised 0–100 visit frequency + avg dwell time)
- **Active anomaly feed** (BILLING_QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE)

---

## Store Layout

The store layout is defined in `data/store_layout.json` (derived from `store 2 - layout.png`):

| Zone | Type | Camera | SKU Zone |
|------|------|--------|----------|
| ENTRANCE | Threshold | CAM_ENTRY_01 | — |
| FOH | Floor | CAM_FLOOR_01 | GENERAL |
| BOH | Staff only | CAM_FLOOR_01 | — |
| CASH_COUNTER | Billing | CAM_BILLING_01 | — |
| WALL_LEFT | Product | CAM_FLOOR_01 | SKINCARE |
| WALL_RIGHT | Product | CAM_FLOOR_01 | COSMETICS |
| WALL_BACK | Product | CAM_FLOOR_01 | HAIRCARE |

---

## Edge Cases Handled

| Edge Case | Handling |
|-----------|---------|
| Group entry (3 people together) | ByteTrack creates 3 independent tracks → 3 ENTRY events with same-timestamp group tag |
| Staff movement | HSV uniform colour classifier → `is_staff=True` on all events, excluded from metrics |
| Re-entry | Cosine similarity (ResNet18 embeddings) against recent_exits with 30-min TTL |
| Partial occlusion | Confidence emitted as-is (not suppressed); API can filter at query time |
| Billing queue buildup | Queue depth tracked in BILLING_QUEUE_JOIN metadata; spike anomaly at depth > 5 |
| Empty store periods | Zero-visitor metrics returned correctly (no divide-by-zero, no null) |
| Camera angle overlap | Cross-camera Re-ID dedup within 30-second window |

---

## Project Structure

```
store-intelligence/
├── README.md              # This file
├── docker-compose.yml     # Start everything: docker compose up
├── Dockerfile.api         # API container
├── Dockerfile.pipeline    # Detection pipeline container
├── requirements.txt       # Python dependencies
├── pipeline/              # CCTV detection and event emission
│   ├── detect.py          # YOLOv8n + ByteTrack
│   ├── tracker.py         # Re-ID + session management
│   ├── staff_detector.py  # Uniform colour classification
│   ├── zone_mapper.py     # Bounding box → zone polygon
│   ├── emit.py            # Event builder + HTTP POST
│   └── run.sh             # One-command pipeline runner
├── app/                   # FastAPI analytics API
│   ├── main.py            # Entrypoint, middleware
│   ├── models.py          # Pydantic schema + ORM
│   ├── ingestion.py       # POST /events/ingest
│   ├── metrics.py         # GET /metrics
│   ├── funnel.py          # GET /funnel
│   ├── heatmap.py         # GET /heatmap
│   ├── anomalies.py       # GET /anomalies
│   ├── health.py          # GET /health
│   └── websocket.py       # WebSocket /ws/{store_id}
├── dashboard/             # React 18 + Vite live dashboard
├── tests/                 # pytest test suite (>70% coverage)
├── data/                  # store_layout.json, pos_transactions.csv
└── docs/                  # DESIGN.md, CHOICES.md, rules.md, logs.md
```

---

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) — Architecture overview + AI-assisted decisions
- [docs/CHOICES.md](docs/CHOICES.md) — Three key engineering decisions with reasoning
- [docs/rules.md](docs/rules.md) — Engineering conventions
- [docs/logs.md](docs/logs.md) — Logging format and conventions

---

## Submission

Repository: (private — invited purplletechchallenge2026@hackerearth.com)

Checklist:
- [x] `docker compose up` starts API + dashboard + database
- [x] `bash pipeline/run.sh` processes all clips → emits events
- [x] `POST /events/ingest` accepts events (idempotent)
- [x] `GET /stores/STORE_BLR_002/metrics` returns valid JSON
- [x] DESIGN.md — AI-Assisted Decisions section ✓
- [x] CHOICES.md — 3 decisions with full reasoning ✓
- [x] Test files have `# PROMPT:` / `# CHANGES MADE:` blocks ✓
- [x] Live dashboard at http://localhost:3000 ✓
