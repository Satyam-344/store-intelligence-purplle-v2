"""
Database engine, session management, and initialisation.
Supports PostgreSQL (production) and SQLite (testing).
"""

from __future__ import annotations

import csv
import os
from contextlib import contextmanager
from datetime import datetime, date, timezone
from typing import Generator

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, POSTransaction

log = structlog.get_logger()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://store_intel:store_intel@db:5432/store_intel",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(pos_csv_path: str | None = None) -> None:
    """Create all tables and optionally seed POS transactions."""
    Base.metadata.create_all(bind=engine)
    log.info("db_initialised", tables=list(Base.metadata.tables.keys()))

    if pos_csv_path and os.path.exists(pos_csv_path):
        _seed_pos_transactions(pos_csv_path)


def _seed_pos_transactions(csv_path: str) -> None:
    with db_session() as db:
        existing = db.query(POSTransaction).count()
        if existing > 0:
            log.info("pos_seed_skipped", reason="already seeded", count=existing)
            return

        today = date.today()
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts_str = row["timestamp"].replace("Z", "+00:00")
                ts_orig = datetime.fromisoformat(ts_str)
                # Remap historical dates to today so metrics queries always see data
                ts_today = ts_orig.replace(
                    year=today.year, month=today.month, day=today.day,
                    tzinfo=timezone.utc,
                )
                rows.append(POSTransaction(
                    store_id=row["store_id"].strip(),
                    transaction_id=row["transaction_id"].strip(),
                    timestamp=ts_today,
                    basket_value_inr=float(row["basket_value_inr"]),
                ))

        db.bulk_save_objects(rows)
        log.info("pos_seeded", count=len(rows), file=csv_path)


def check_db_health() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("db_health_check_failed", error=str(exc))
        return False
