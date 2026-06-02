"""
Pydantic v2 event schema + SQLAlchemy ORM models.
Canonical event format as defined in challenge Section 4.
Extended with demographic fields observed in sample_events.jsonl.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


# ─── Enums ───────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class AnomalySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


# ─── SQLAlchemy ORM ───────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class EventRecord(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False)
    store_id = Column(String(50), nullable=False, index=True)
    camera_id = Column(String(50), nullable=False)
    visitor_id = Column(String(50), nullable=False, index=True)
    event_type = Column(String(30), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    zone_id = Column(String(50), nullable=True)
    dwell_ms = Column(Integer, nullable=False, default=0)
    is_staff = Column(Boolean, nullable=False, default=False)
    confidence = Column(Float, nullable=False)
    event_metadata = Column("metadata", JSON, nullable=True)
    ingested_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_id"),
        Index("ix_events_store_ts", "store_id", "timestamp"),
        Index("ix_events_store_type", "store_id", "event_type"),
        Index("ix_events_visitor", "visitor_id"),
    )


class POSTransaction(Base):
    __tablename__ = "pos_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(50), nullable=False, index=True)
    transaction_id = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    basket_value_inr = Column(Numeric(10, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_transaction_id"),
        Index("ix_pos_store_ts", "store_id", "timestamp"),
    )


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    gender_pred: Optional[str] = None
    age_bucket: Optional[str] = None
    queue_position_at_join: Optional[int] = None
    wait_seconds: Optional[int] = None

    model_config = {"extra": "allow"}


class EventCreate(BaseModel):
    event_id: str = Field(..., description="UUID v4 — globally unique per event")
    store_id: str = Field(..., min_length=1, max_length=50)
    camera_id: str = Field(..., min_length=1, max_length=50)
    visitor_id: str = Field(..., min_length=1, max_length=50)
    event_type: EventType
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp ending in Z")
    zone_id: Optional[str] = Field(None, max_length=50)
    dwell_ms: int = Field(0, ge=0)
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: Optional[EventMetadata] = None

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        if not _UUID_RE.match(v):
            raise ValueError(f"event_id must be a valid UUID v4, got: {v!r}")
        return v.lower()

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        if not v.endswith("Z"):
            raise ValueError("timestamp must be ISO-8601 UTC ending in Z")
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"timestamp is not valid ISO-8601: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_zone_id_for_entry_exit(self) -> "EventCreate":
        if self.event_type in (EventType.ENTRY, EventType.EXIT) and self.zone_id is not None:
            raise ValueError("zone_id must be null for ENTRY and EXIT events")
        return self

    @model_validator(mode="after")
    def validate_queue_depth_for_billing(self) -> "EventCreate":
        if self.event_type == EventType.BILLING_QUEUE_JOIN:
            if self.metadata is None or self.metadata.queue_depth is None:
                raise ValueError("BILLING_QUEUE_JOIN requires metadata.queue_depth")
        return self


class IngestRequest(BaseModel):
    events: list[EventCreate] = Field(..., max_length=500)


class IngestError(BaseModel):
    event_id: str
    error: str


class IngestResponse(BaseModel):
    ingested: int
    duplicates: int
    failed: int
    errors: list[IngestError] = []


# ─── Metrics Response ─────────────────────────────────────────────────────────

class ZoneDwellStat(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visit_count: int


class MetricsResponse(BaseModel):
    store_id: str
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: list[ZoneDwellStat]
    current_queue_depth: int
    abandonment_rate: float
    total_transactions: int
    avg_basket_value_inr: Optional[float]


# ─── Funnel Response ─────────────────────────────────────────────────────────

class FunnelStage(BaseModel):
    stage: str
    visitor_count: int
    drop_off_pct: float


class FunnelResponse(BaseModel):
    store_id: str
    window_start: str
    window_end: str
    stages: list[FunnelStage]


# ─── Heatmap Response ────────────────────────────────────────────────────────

class HeatmapZone(BaseModel):
    zone_id: str
    zone_name: str
    visit_frequency: int
    avg_dwell_ms: float
    normalised_score: float


class HeatmapResponse(BaseModel):
    store_id: str
    data_confidence: bool
    session_count: int
    zones: list[HeatmapZone]


# ─── Anomaly Response ────────────────────────────────────────────────────────

class Anomaly(BaseModel):
    anomaly_id: str
    anomaly_type: str
    severity: AnomalySeverity
    detected_at: str
    description: str
    suggested_action: str
    metadata: dict[str, Any] = {}


class AnomaliesResponse(BaseModel):
    store_id: str
    active_anomalies: list[Anomaly]
    checked_at: str


# ─── Health Response ─────────────────────────────────────────────────────────

class StoreHealthStatus(BaseModel):
    store_id: str
    last_event_timestamp: Optional[str]
    lag_seconds: Optional[float]
    status: str


class HealthResponse(BaseModel):
    status: str
    version: str
    db_status: str
    stores: list[StoreHealthStatus]
    checked_at: str


# ─── WebSocket push payload ───────────────────────────────────────────────────

class LiveUpdate(BaseModel):
    store_id: str
    event_type: str
    unique_visitors: int
    current_queue_depth: int
    conversion_rate: float
    latest_anomaly: Optional[str] = None
    timestamp: str
