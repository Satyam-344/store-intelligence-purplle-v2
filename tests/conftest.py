# PROMPT: Create pytest fixtures for an in-memory SQLite database, a FastAPI test client,
#         and helper functions to insert test events. The app uses SQLAlchemy 2.x and Pydantic v2.
#         Include fixtures for: empty store, all-staff clip scenario, re-entry scenario.
# CHANGES MADE: Added explicit UTC timezone handling; replaced SQLite in-memory with
#               file-based SQLite per test to avoid connection sharing issues; added
#               pos_transaction fixture for conversion rate tests.

import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker, Session

from app.models import Base, EventRecord, EventType, POSTransaction
from app.database import get_db
from app.main import app

TEST_DB_URL = "sqlite:///./test_store.db"


@pytest.fixture(scope="function")
def test_engine():
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
    )

    @sqlalchemy_event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    import os
    try:
        os.remove("./test_store.db")
    except FileNotFoundError:
        pass


@pytest.fixture(scope="function")
def db(test_engine) -> Generator[Session, None, None]:
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    from unittest.mock import patch
    with patch("app.main.init_db"):  # prevent lifespan from hitting PostgreSQL
        with patch("app.database.check_db_health", return_value=True):
            with patch("app.health.check_db_health", return_value=True):
                with patch("app.ingestion.check_db_health", return_value=True):
                    with patch("app.metrics.check_db_health", return_value=True):
                        with patch("app.funnel.check_db_health", return_value=True):
                            with patch("app.heatmap.check_db_health", return_value=True):
                                with patch("app.anomalies.check_db_health", return_value=True):
                                    with patch("app.websocket.broadcast_update"):
                                        with TestClient(app) as c:
                                            yield c

    app.dependency_overrides.clear()


STORE_ID = "STORE_BLR_002"


def make_event(
    *,
    store_id: str = STORE_ID,
    camera_id: str = "CAM_ENTRY_01",
    visitor_id: str = None,
    event_type: str = "ENTRY",
    timestamp: datetime = None,
    zone_id: str = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.9,
    metadata: dict = None,
    event_id: str = None,
) -> dict:
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    if visitor_id is None:
        visitor_id = f"VIS_{uuid.uuid4().hex[:8]}"
    if event_id is None:
        event_id = str(uuid.uuid4())
    return {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": metadata or {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


def insert_event(db: Session, **kwargs) -> EventRecord:
    ev = make_event(**kwargs)
    ts = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
    record = EventRecord(
        event_id=ev["event_id"],
        store_id=ev["store_id"],
        camera_id=ev["camera_id"],
        visitor_id=ev["visitor_id"],
        event_type=ev["event_type"],
        timestamp=ts,
        zone_id=ev.get("zone_id"),
        dwell_ms=ev.get("dwell_ms", 0),
        is_staff=ev.get("is_staff", False),
        confidence=ev.get("confidence", 0.9),
        event_metadata=ev.get("metadata", {}),
        ingested_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    return record


def insert_pos_transaction(db: Session, store_id: str = STORE_ID, amount: float = 999.0) -> POSTransaction:
    tx = POSTransaction(
        store_id=store_id,
        transaction_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        basket_value_inr=amount,
    )
    db.add(tx)
    db.commit()
    return tx
