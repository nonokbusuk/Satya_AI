import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from .database import engine
from .core.audit_logger import AuditLogger
from .routers.policies import router as policies_router
from .routers.agents import router as agents_router
from .routers.audit import router as audit_router
from .routers.evaluate import router as evaluate_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    audit_logger = AuditLogger()
    app.state.audit_logger = audit_logger
    await audit_logger.start()
    try:
        yield
    finally:
        await audit_logger.stop()


app = FastAPI(
    title="Satya AI",
    description="Agentic Governance Framework",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(policies_router)
app.include_router(agents_router)
app.include_router(audit_router)
app.include_router(evaluate_router)


@app.get("/health")
async def health(request: Request):
    """Readiness probe: only report healthy when database and audit worker are live."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.exception("Health check failed: database connection unavailable")
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "checks": {"database": "unavailable"}},
        ) from exc

    audit_logger = getattr(request.app.state, "audit_logger", None)
    worker = getattr(audit_logger, "worker_task", None)
    if (
        worker is None
        or worker.done()
        or getattr(audit_logger, "last_error", None)
        or getattr(audit_logger, "failed_writes", 0) > 0
    ):
        logger.error("Health check failed: audit worker is unavailable or has write errors")
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "checks": {"audit_worker": "unavailable"}},
        )

    return {"status": "ok", "checks": {"database": "ok", "audit_worker": "ok"}}
