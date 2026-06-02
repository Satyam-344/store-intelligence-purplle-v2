"""
FastAPI entrypoint — routers, middleware, startup, exception handling.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.database import DATABASE_URL, check_db_health, init_db
from app.ingestion import router as ingest_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router
from app.websocket import router as ws_router

log = structlog.get_logger()

POS_CSV = os.environ.get("POS_CSV_PATH", "/data/pos_transactions.csv")
VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api_starting", version=VERSION)
    init_db(pos_csv_path=POS_CSV)
    if DATABASE_URL.startswith("sqlite"):
        from app.seed_demo import seed_demo
        from app.database import db_session
        seed_demo(db_session)
    log.info("api_ready")
    yield
    log.info("api_shutting_down")


app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV footage — Purplle Engineering Challenge",
    version=VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next) -> Response:
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id

    store_id = request.path_params.get("store_id")
    start = time.perf_counter()

    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    try:
        response = await call_next(request)
    except Exception as exc:
        log.error(
            "unhandled_exception",
            trace_id=trace_id,
            endpoint=str(request.url.path),
            error=str(exc),
        )
        response = JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_ERROR", "detail": "An unexpected error occurred"},
        )
    finally:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info(
            "request_complete",
            trace_id=trace_id,
            store_id=store_id,
            endpoint=str(request.url.path),
            method=request.method,
            latency_ms=latency_ms,
            status_code=response.status_code,
        )
        structlog.contextvars.unbind_contextvars("trace_id")

    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_error", error=str(exc), path=str(request.url.path))
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "detail": "An unexpected error occurred"},
    )


app.include_router(ingest_router, prefix="/events", tags=["Ingestion"])
app.include_router(metrics_router, prefix="/stores", tags=["Metrics"])
app.include_router(funnel_router, prefix="/stores", tags=["Funnel"])
app.include_router(heatmap_router, prefix="/stores", tags=["Heatmap"])
app.include_router(anomalies_router, prefix="/stores", tags=["Anomalies"])
app.include_router(health_router, tags=["Health"])
app.include_router(ws_router, tags=["WebSocket"])
