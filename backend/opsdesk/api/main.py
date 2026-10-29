"""OpsDesk API application (FastAPI).

Error bodies keep the ``{"detail": ...}`` shape the web client already parses;
validation errors raised by the services keep the field-keyed shape.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..db import session as db_session
from ..errors import OpsDeskError
from ..settings import settings
from .db import db_manager
from .routers import (admin_audit, admin_config, admin_overview, admin_people, admin_sla, ai, auth, catalog, channels, cmdb, csat, kb,
                      notifications, reports, tickets)

if sys.platform == "win32":  # psycopg's async driver cannot run on the Windows Proactor event loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("opsdesk.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing OpsDesk API...")
    try:
        await db_manager.open()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database pool initialization deferred or failed: %s", exc)
    yield
    logger.info("Shutting down OpsDesk API...")
    try:
        await db_manager.close()
    except Exception:  # noqa: BLE001
        pass
    db_session.dispose_engines()


app = FastAPI(
    title="OpsDesk API", version="2.0.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)

if settings.cors_allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_allowed_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """nosniff, referrer policy and (outside debug) HSTS. nginx adds CSP and framing headers for the web app."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if not settings.debug and settings.secure_hsts_seconds:
        response.headers.setdefault("Strict-Transport-Security",
                                    f"max-age={settings.secure_hsts_seconds}; includeSubDomains")
    return response


@app.get("/healthz", tags=["health"])
@app.get("/api/healthz", tags=["health"])
async def healthz():
    """HTTP 200 {'status': 'ok'} when the database is reachable, else 503. Never exposes credentials or traces."""
    if await db_manager.ping() or await run_in_threadpool(db_session.ping):
        return {"status": "ok"}
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "db_unavailable"})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(OpsDeskError)
async def domain_exception_handler(request: Request, exc: OpsDeskError):
    if isinstance(exc.detail, (dict, list)):
        return JSONResponse(status_code=exc.http_status, content=exc.detail)
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": errors})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled internal server error: %s", type(exc).__name__, exc_info=False)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={"detail": "An unexpected server error occurred."})


for module in (auth, cmdb, tickets, catalog, csat, kb, ai, channels, reports, notifications, admin_audit, admin_people,
               admin_sla, admin_config, admin_overview):
    app.include_router(module.router)
