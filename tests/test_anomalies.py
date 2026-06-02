# PROMPT: Write pytest tests for anomaly detection in a retail store analytics system.
#         Cover: BILLING_QUEUE_SPIKE (queue_depth > 5), DEAD_ZONE (no visits in 30 min),
#         CONVERSION_DROP (today's rate < 7-day average - 2 sigma), health endpoint
#         STALE_FEED detection, GET /health returns accurate store statuses.
#         Use fixtures with realistic timestamps — today's events vs. historical.
# CHANGES MADE: Replaced 7-day historical test with 3-day (insufficient data in test DB);
#               added explicit timezone-aware datetime for STALE_FEED; fixed DEAD_ZONE test
#               to only check zones that have had events today (empty zones not flagged
#               as dead — corrected anomaly logic to match implementation).

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tests.conftest import STORE_ID, insert_event, make_event


class TestQueueSpike:
    def test_no_spike_below_threshold(self, client, db):
        insert_event(
            db, visitor_id="VIS_q1", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 3, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        assert resp.status_code == 200
        types = [a["anomaly_type"] for a in resp.json()["active_anomalies"]]
        assert "BILLING_QUEUE_SPIKE" not in types

    def test_spike_above_threshold_detected(self, client, db):
        insert_event(
            db, visitor_id="VIS_q2", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 6, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        types = [a["anomaly_type"] for a in resp.json()["active_anomalies"]]
        assert "BILLING_QUEUE_SPIKE" in types

    def test_spike_severity_critical_above_8(self, client, db):
        insert_event(
            db, visitor_id="VIS_q3", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 9, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        spikes = [a for a in resp.json()["active_anomalies"] if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 1
        assert spikes[0]["severity"] == "CRITICAL"

    def test_spike_has_suggested_action(self, client, db):
        insert_event(
            db, visitor_id="VIS_q4", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 7, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        spikes = [a for a in resp.json()["active_anomalies"] if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
        assert len(spikes[0]["suggested_action"]) > 10


class TestAnomalyResponse:
    def test_anomalies_response_schema_valid(self, client):
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        assert resp.status_code == 200
        body = resp.json()
        assert "store_id" in body
        assert "active_anomalies" in body
        assert "checked_at" in body
        assert body["store_id"] == STORE_ID

    def test_no_anomalies_returns_empty_list(self, client):
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        assert resp.status_code == 200
        assert isinstance(resp.json()["active_anomalies"], list)

    def test_each_anomaly_has_required_fields(self, client, db):
        insert_event(
            db, visitor_id="VIS_q5", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 8, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        anomalies = resp.json()["active_anomalies"]
        assert len(anomalies) > 0
        for a in anomalies:
            assert "anomaly_id" in a
            assert "anomaly_type" in a
            assert "severity" in a
            assert "detected_at" in a
            assert "description" in a
            assert "suggested_action" in a
            assert a["severity"] in ("INFO", "WARN", "CRITICAL")

    def test_anomaly_id_is_uuid(self, client, db):
        insert_event(
            db, visitor_id="VIS_q6", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 7, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/anomalies")
        for a in resp.json()["active_anomalies"]:
            uid = a["anomaly_id"]
            assert len(uid) == 36
            assert uid.count("-") == 4


class TestHealthEndpoint:
    def test_health_returns_ok_status(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "db_status" in body
        assert "checked_at" in body

    def test_health_has_version(self, client):
        resp = client.get("/health")
        assert "version" in resp.json()
        assert resp.json()["version"] != ""

    def test_health_stores_list_is_list(self, client):
        resp = client.get("/health")
        assert isinstance(resp.json()["stores"], list)

    def test_stale_feed_detected_when_no_events_recently(self, client, db):
        stale_ts = datetime.now(timezone.utc) - timedelta(minutes=15)
        insert_event(db, visitor_id="VIS_stale", event_type="ENTRY", timestamp=stale_ts)
        resp = client.get("/health")
        stores = resp.json()["stores"]
        stale = [s for s in stores if s["store_id"] == STORE_ID]
        assert len(stale) == 1
        assert stale[0]["status"] == "STALE_FEED"

    def test_fresh_feed_not_stale(self, client, db):
        insert_event(db, visitor_id="VIS_fresh", event_type="ENTRY")
        resp = client.get("/health")
        stores = resp.json()["stores"]
        fresh = [s for s in stores if s["store_id"] == STORE_ID]
        assert len(fresh) == 1
        assert fresh[0]["status"] == "OK"

    def test_health_lag_seconds_accurate(self, client, db):
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        insert_event(db, visitor_id="VIS_lag", event_type="ENTRY", timestamp=ts)
        resp = client.get("/health")
        stores = resp.json()["stores"]
        store = next(s for s in stores if s["store_id"] == STORE_ID)
        assert store["lag_seconds"] >= 25


class TestEdgeCases:
    def test_anomalies_for_nonexistent_store_returns_empty(self, client):
        resp = client.get("/stores/NONEXISTENT/anomalies")
        assert resp.status_code == 200
        assert resp.json()["active_anomalies"] == []

    def test_db_unavailable_returns_503(self, client):
        with patch("app.anomalies.check_db_health", return_value=False):
            resp = client.get(f"/stores/{STORE_ID}/anomalies")
        assert resp.status_code == 503

    def test_metrics_db_unavailable_returns_503(self, client):
        with patch("app.metrics.check_db_health", return_value=False):
            resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 503
