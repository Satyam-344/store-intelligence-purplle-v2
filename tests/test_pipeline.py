# PROMPT: Write pytest tests for the event schema validation layer of a store intelligence
#         system. Test: UUID v4 format enforcement, ISO-8601 UTC timestamp validation,
#         zone_id null constraint for ENTRY/EXIT events, confidence range 0-1,
#         BILLING_QUEUE_JOIN requires queue_depth, event_id uniqueness in the database,
#         schema compliance for all 8 event types, and group entry (multiple ENTRY events
#         in a 2-second window should all be distinct).
# CHANGES MADE: Removed test for async ingest (used sync TestClient instead); added
#               explicit UTC timezone to all timestamps; added REENTRY event test;
#               fixed: zone_id validation test — entry with zone_id must return 422.

import uuid
from datetime import datetime, timezone

import pytest

from tests.conftest import STORE_ID, insert_event, make_event


class TestSchemaValidation:
    def test_valid_entry_event_accepted(self, client):
        event = make_event(event_type="ENTRY", zone_id=None)
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

    def test_entry_with_zone_id_rejected(self, client):
        event = make_event(event_type="ENTRY", zone_id="WALL_LEFT")
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_exit_with_zone_id_rejected(self, client):
        event = make_event(event_type="EXIT", zone_id="WALL_LEFT")
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_invalid_uuid_rejected(self, client):
        event = make_event()
        event["event_id"] = "not-a-uuid"
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_invalid_timestamp_no_z_rejected(self, client):
        event = make_event()
        event["timestamp"] = "2026-03-03T14:22:10"
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_confidence_out_of_range_rejected(self, client):
        event = make_event(confidence=1.5)
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_billing_queue_join_requires_queue_depth(self, client):
        event = make_event(
            event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
        )
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 422

    def test_billing_queue_join_with_depth_accepted(self, client):
        event = make_event(
            event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 3, "sku_zone": None, "session_seq": 1},
        )
        resp = client.post("/events/ingest", json={"events": [event]})
        assert resp.status_code == 200

    def test_all_event_types_schema_valid(self, client):
        now = datetime.now(timezone.utc)
        events = [
            make_event(event_type="ENTRY", zone_id=None, timestamp=now),
            make_event(event_type="EXIT", zone_id=None, timestamp=now),
            make_event(event_type="ZONE_ENTER", zone_id="FOH", timestamp=now),
            make_event(event_type="ZONE_EXIT", zone_id="FOH", timestamp=now),
            make_event(event_type="ZONE_DWELL", zone_id="FOH", dwell_ms=30000, timestamp=now),
            make_event(
                event_type="BILLING_QUEUE_JOIN",
                zone_id="CASH_COUNTER",
                metadata={"queue_depth": 2, "sku_zone": None, "session_seq": 3},
                timestamp=now,
            ),
            make_event(event_type="BILLING_QUEUE_ABANDON", zone_id="CASH_COUNTER", timestamp=now),
            make_event(event_type="REENTRY", zone_id=None, timestamp=now),
        ]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 8


class TestIdempotency:
    def test_duplicate_event_id_is_idempotent(self, client):
        event = make_event()
        resp1 = client.post("/events/ingest", json={"events": [event]})
        resp2 = client.post("/events/ingest", json={"events": [event]})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["ingested"] == 1
        assert resp2.json()["duplicates"] == 1
        assert resp2.json()["ingested"] == 0

    def test_same_payload_twice_returns_same_response_shape(self, client):
        event = make_event()
        resp1 = client.post("/events/ingest", json={"events": [event]})
        resp2 = client.post("/events/ingest", json={"events": [event]})
        assert set(resp1.json().keys()) == set(resp2.json().keys())

    def test_partial_duplicate_batch(self, client):
        ev1 = make_event(visitor_id="VIS_aaa111")
        ev2 = make_event(visitor_id="VIS_bbb222")
        client.post("/events/ingest", json={"events": [ev1]})
        resp = client.post("/events/ingest", json={"events": [ev1, ev2]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] == 1
        assert body["duplicates"] == 1


class TestGroupEntry:
    def test_three_simultaneous_entries_produce_three_events(self, client, db):
        now = datetime.now(timezone.utc)
        events = [
            make_event(visitor_id=f"VIS_{i:06d}", event_type="ENTRY", timestamp=now)
            for i in range(3)
        ]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 3

    def test_group_entry_counts_as_three_unique_visitors(self, client, db, monkeypatch):
        now = datetime.now(timezone.utc)
        events = [
            make_event(visitor_id=f"VIS_group_{i}", event_type="ENTRY", timestamp=now)
            for i in range(3)
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 3


class TestBatchLimits:
    def test_batch_of_500_accepted(self, client):
        events = [make_event() for _ in range(500)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200

    def test_batch_of_501_rejected(self, client):
        events = [make_event() for _ in range(501)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 422
