#!/usr/bin/env bash
# One command to process all 4 CCTV clips → emit events to the API.
# Usage: bash pipeline/run.sh [--api-base http://localhost:8000] [--store-id STORE_BLR_002]
# Output: data/output_events.jsonl (all emitted events for debugging)

set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
STORE_ID="${STORE_ID:-STORE_BLR_002}"
CLIP_START="${CLIP_START:-$(date -u +%Y-%m-%dT10:00:00Z)}"

# In Docker the repo root is mounted at /clips; locally use the script's parent dir
if [ -d "/clips" ]; then
    CLIPS_DIR="${CLIPS_DIR:-/clips}"
    DATA_DIR="${DATA_DIR:-/data}"
else
    CLIPS_DIR="${CLIPS_DIR:-$(dirname "$0")/..}"
    DATA_DIR="${DATA_DIR:-$(dirname "$0")/../data}"
fi
OUTPUT_JSONL="$DATA_DIR/output_events.jsonl"

echo "=== Store Intelligence Detection Pipeline ==="
echo "API: $API_BASE"
echo "Store: $STORE_ID"
echo "Output: $OUTPUT_JSONL"
echo ""

# Wait for API to be ready
echo "Waiting for API to be ready..."
for i in {1..30}; do
  if curl -sf "$API_BASE/health" > /dev/null 2>&1; then
    echo "API is ready."
    break
  fi
  echo "  Attempt $i/30 — retrying in 2s..."
  sleep 2
done

rm -f "$OUTPUT_JSONL"

echo ""
echo "Processing entry 1.mp4 (CAM_ENTRY_01)..."
python -m pipeline.detect \
  --clip "$CLIPS_DIR/entry 1.mp4" \
  --camera-id CAM_ENTRY_01 \
  --store-id "$STORE_ID" \
  --clip-start "$CLIP_START" \
  --api-base "$API_BASE" \
  --output-jsonl "$OUTPUT_JSONL"

echo ""
echo "Processing entry 2.mp4 (CAM_ENTRY_02)..."
python -m pipeline.detect \
  --clip "$CLIPS_DIR/entry 2.mp4" \
  --camera-id CAM_ENTRY_02 \
  --store-id "$STORE_ID" \
  --clip-start "$CLIP_START" \
  --api-base "$API_BASE" \
  --output-jsonl "$OUTPUT_JSONL"

echo ""
echo "Processing billing_area.mp4 (CAM_BILLING_01)..."
python -m pipeline.detect \
  --clip "$CLIPS_DIR/billing_area.mp4" \
  --camera-id CAM_BILLING_01 \
  --store-id "$STORE_ID" \
  --clip-start "$CLIP_START" \
  --api-base "$API_BASE" \
  --output-jsonl "$OUTPUT_JSONL"

echo ""
echo "Processing zone.mp4 (CAM_FLOOR_01)..."
python -m pipeline.detect \
  --clip "$CLIPS_DIR/zone.mp4" \
  --camera-id CAM_FLOOR_01 \
  --store-id "$STORE_ID" \
  --clip-start "$CLIP_START" \
  --api-base "$API_BASE" \
  --output-jsonl "$OUTPUT_JSONL"

echo ""
echo "=== Pipeline complete ==="
echo "Events written to: $OUTPUT_JSONL"
echo "Total events: $(wc -l < "$OUTPUT_JSONL" 2>/dev/null || echo 0)"
echo ""
echo "Check metrics: curl $API_BASE/stores/$STORE_ID/metrics"
echo "Check health:  curl $API_BASE/health"
