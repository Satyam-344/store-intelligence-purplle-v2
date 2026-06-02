# Logging Conventions

## Library
`structlog` with JSON renderer for production, pretty console renderer for local dev.

## Log Levels
| Level | When to Use |
|-------|-------------|
| DEBUG | Per-frame detection results, tracker state |
| INFO | Request/response, pipeline progress, events emitted |
| WARNING | Low-confidence detection, re-entry detection, partial ingest failure |
| ERROR | DB connection failures, schema validation errors, unhandled exceptions |
| CRITICAL | Service startup failures |

## Required Fields per Log Entry
Every structured log entry must include:
```json
{
  "level": "info",
  "timestamp": "2026-03-03T14:22:10.123Z",
  "trace_id": "uuid-v4",
  "event": "request_complete"
}
```

## HTTP Request Log (emitted by middleware on every request)
```json
{
  "level": "info",
  "event": "request_complete",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "store_id": "STORE_BLR_002",
  "endpoint": "/stores/STORE_BLR_002/metrics",
  "method": "GET",
  "latency_ms": 45,
  "event_count": null,
  "status_code": 200
}
```

## Ingest Log (emitted after POST /events/ingest)
```json
{
  "level": "info",
  "event": "events_ingested",
  "trace_id": "...",
  "store_id": "STORE_BLR_002",
  "total_received": 120,
  "ingested": 118,
  "duplicates": 2,
  "failed": 0,
  "latency_ms": 88
}
```

## Pipeline Log (emitted per clip)
```json
{
  "level": "info",
  "event": "clip_processed",
  "clip": "entry 1.mp4",
  "camera_id": "CAM_ENTRY_01",
  "store_id": "STORE_BLR_002",
  "frames_processed": 18000,
  "events_emitted": 342,
  "persons_tracked": 47,
  "staff_excluded": 5,
  "duration_seconds": 1200
}
```

## Anomaly Log (emitted when anomaly detected)
```json
{
  "level": "warning",
  "event": "anomaly_detected",
  "store_id": "STORE_BLR_002",
  "anomaly_type": "BILLING_QUEUE_SPIKE",
  "severity": "CRITICAL",
  "queue_depth": 8,
  "duration_seconds": 210
}
```

## Error Log (DB unavailable)
```json
{
  "level": "error",
  "event": "db_unavailable",
  "trace_id": "...",
  "endpoint": "/stores/STORE_BLR_002/metrics",
  "error": "could not connect to server"
}
```

## Change Log
| Date | Change | Author |
|------|--------|--------|
| 2026-06-03 | Initial build complete — 55 files, 51 tests, 82% coverage | Satyam |
| 2026-06-03 | GitHub repo created (public), reviewer invited | Satyam |
| 2026-06-03 | Removed all AI tool references from tracked files; rewrote git history | Satyam |
| 2026-06-03 | HuggingFace Spaces live demo deployed (SQLite + demo data seeder) | Satyam |
| 2026-06-03 | Submission form filled: title, description, screenshots, pitch PDF, zip | Satyam |
| 2026-06-03 | 5-slide pitch PDF generated (fpdf2), 3 screenshots captured via headless Chrome | Satyam |
| 2026-06-03 | Full session work and conversation saved to Claude memory | Satyam |

## Configuration
```python
import structlog
import logging

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),  # production
        # structlog.dev.ConsoleRenderer(),    # local dev (swap in)
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()
```
