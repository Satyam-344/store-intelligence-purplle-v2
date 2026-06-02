# PROMPT: Write pytest tests for a retail analytics API's /metrics, /funnel, and /heatmap
#         endpoints. Cover: zero visitors (empty store), all-staff clip (no customer events),
#         zero purchases (conversion rate = 0), re-entry deduplication in funnel,
#         heatmap data_confidence=False when fewer than 20 sessions,
#         correct conversion rate formula (billing_visitors / unique_visitors).
# CHANGES MADE: Added is_staff=True fixture for all-staff scenario; patched check_db_health
#               to avoid PostgreSQL dependency; used insert_event helper for DRY setup;
#               added explicit today's date check for date-partitioned queries.

import uuid
from datetime import datetime, timezone

import pytest

from tests.conftest import STORE_ID, insert_event, insert_pos_transaction, make_event


class TestMetricsEmptyStore:
    def test_empty_store_returns_zero_visitors(self, client):
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["unique_visitors"] == 0
        assert body["conversion_rate"] == 0.0
        assert body["current_queue_depth"] == 0
        assert body["abandonment_rate"] == 0.0
        assert body["avg_dwell_per_zone"] == []

    def test_empty_store_does_not_crash(self, client):
        resp = client.get("/stores/NONEXISTENT_STORE/metrics")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 0

    def test_zero_purchases_gives_zero_conversion(self, client, db):
        insert_event(db, visitor_id="VIS_001", event_type="ENTRY", zone_id=None)
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        assert resp.json()["conversion_rate"] == 0.0
        assert resp.json()["total_transactions"] == 0
        assert resp.json()["avg_basket_value_inr"] is None


class TestMetricsStaffExclusion:
    def test_staff_events_excluded_from_unique_visitors(self, client, db):
        insert_event(db, visitor_id="VIS_staff_01", event_type="ENTRY", is_staff=True)
        insert_event(db, visitor_id="VIS_cust_01", event_type="ENTRY", is_staff=False)
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 1

    def test_all_staff_clip_returns_zero_visitors(self, client, db):
        for i in range(5):
            insert_event(db, visitor_id=f"VIS_staff_{i:02d}", event_type="ENTRY", is_staff=True)
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 0

    def test_staff_zone_dwell_excluded_from_avg_dwell(self, client, db):
        insert_event(
            db, visitor_id="VIS_staff_zone", event_type="ZONE_DWELL",
            zone_id="FOH", dwell_ms=60000, is_staff=True
        )
        insert_event(
            db, visitor_id="VIS_cust_zone", event_type="ZONE_DWELL",
            zone_id="FOH", dwell_ms=15000, is_staff=False
        )
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        zones = {z["zone_id"]: z for z in resp.json()["avg_dwell_per_zone"]}
        assert zones["FOH"]["avg_dwell_ms"] == 15000.0


class TestMetricsConversionRate:
    def test_correct_conversion_rate(self, client, db):
        for i in range(4):
            insert_event(db, visitor_id=f"VIS_cust_{i}", event_type="ENTRY")
        insert_event(db, visitor_id="VIS_cust_0", event_type="ZONE_ENTER", zone_id="CASH_COUNTER")
        insert_event(db, visitor_id="VIS_cust_1", event_type="ZONE_ENTER", zone_id="CASH_COUNTER")
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.json()["conversion_rate"] == pytest.approx(0.5, abs=0.01)

    def test_100_percent_conversion_handled(self, client, db):
        insert_event(db, visitor_id="VIS_solo", event_type="ENTRY")
        insert_event(db, visitor_id="VIS_solo", event_type="ZONE_ENTER", zone_id="CASH_COUNTER")
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.json()["conversion_rate"] == pytest.approx(1.0, abs=0.01)

    def test_queue_depth_reflects_latest_join(self, client, db):
        insert_event(
            db, visitor_id="VIS_q1", event_type="BILLING_QUEUE_JOIN",
            zone_id="CASH_COUNTER",
            metadata={"queue_depth": 4, "sku_zone": None, "session_seq": 1}
        )
        resp = client.get(f"/stores/{STORE_ID}/metrics")
        assert resp.json()["current_queue_depth"] == 4


class TestFunnel:
    def test_funnel_has_four_stages(self, client):
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        assert resp.status_code == 200
        stages = {s["stage"] for s in resp.json()["stages"]}
        assert stages == {"ENTRY", "ZONE_VISIT", "BILLING_QUEUE", "PURCHASE"}

    def test_empty_funnel_has_zero_counts(self, client):
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        for stage in resp.json()["stages"]:
            assert stage["visitor_count"] == 0

    def test_reentry_not_double_counted_in_funnel(self, client, db):
        vid = "VIS_returnee"
        insert_event(db, visitor_id=vid, event_type="ENTRY")
        insert_event(db, visitor_id=vid, event_type="EXIT")
        insert_event(db, visitor_id=vid, event_type="REENTRY")
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        entry_stage = next(s for s in resp.json()["stages"] if s["stage"] == "ENTRY")
        assert entry_stage["visitor_count"] == 1

    def test_staff_excluded_from_funnel(self, client, db):
        insert_event(db, visitor_id="VIS_staff_funnel", event_type="ENTRY", is_staff=True)
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        entry_stage = next(s for s in resp.json()["stages"] if s["stage"] == "ENTRY")
        assert entry_stage["visitor_count"] == 0

    def test_funnel_dropoff_pct_sums_correctly(self, client, db):
        for i in range(4):
            insert_event(db, visitor_id=f"VIS_funnel_{i}", event_type="ENTRY")
        for i in range(3):
            insert_event(db, visitor_id=f"VIS_funnel_{i}", event_type="ZONE_ENTER", zone_id="FOH")
        resp = client.get(f"/stores/{STORE_ID}/funnel")
        stages = {s["stage"]: s for s in resp.json()["stages"]}
        assert stages["ZONE_VISIT"]["drop_off_pct"] == pytest.approx(25.0, abs=1.0)


class TestHeatmap:
    def test_empty_heatmap_returns_no_zones(self, client):
        resp = client.get(f"/stores/{STORE_ID}/heatmap")
        assert resp.status_code == 200
        assert resp.json()["zones"] == []
        assert resp.json()["data_confidence"] is False

    def test_data_confidence_false_under_20_sessions(self, client, db):
        for i in range(10):
            insert_event(db, visitor_id=f"VIS_heat_{i}", event_type="ENTRY")
            insert_event(db, visitor_id=f"VIS_heat_{i}", event_type="ZONE_ENTER", zone_id="FOH")
        resp = client.get(f"/stores/{STORE_ID}/heatmap")
        assert resp.json()["data_confidence"] is False

    def test_normalised_score_max_is_100(self, client, db):
        for i in range(5):
            insert_event(db, visitor_id=f"VIS_top_{i}", event_type="ZONE_ENTER", zone_id="FOH")
        insert_event(db, visitor_id="VIS_low", event_type="ZONE_ENTER", zone_id="WALL_LEFT")
        resp = client.get(f"/stores/{STORE_ID}/heatmap")
        scores = [z["normalised_score"] for z in resp.json()["zones"]]
        assert max(scores) == 100.0

    def test_heatmap_excludes_staff(self, client, db):
        insert_event(
            db, visitor_id="VIS_staff_heat", event_type="ZONE_ENTER",
            zone_id="FOH", is_staff=True
        )
        insert_event(
            db, visitor_id="VIS_cust_heat", event_type="ZONE_ENTER",
            zone_id="WALL_LEFT", is_staff=False
        )
        resp = client.get(f"/stores/{STORE_ID}/heatmap")
        zone_ids = [z["zone_id"] for z in resp.json()["zones"]]
        assert "WALL_LEFT" in zone_ids
        assert "FOH" not in zone_ids
