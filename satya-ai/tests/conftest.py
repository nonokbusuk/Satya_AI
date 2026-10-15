import os
import pytest
import pytest_asyncio

# These are test-only values. Never copy them into a deployed environment.
os.environ.setdefault("SATYA_AGENT_KEYS", "nested-ci-agent-key-not-for-production")
os.environ.setdefault("AUDIT_SECRET", "nested-ci-audit-secret-at-least-32-characters-long")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from satya.database import Base, get_db
from satya.main import app
from httpx import AsyncClient, ASGITransport
from satya.core.audit_logger import AuditLogger


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    AsyncSessionLocal = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_engine, monkeypatch):
    AsyncSessionLocalTest = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with AsyncSessionLocalTest() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("satya.core.agent_registry.AsyncSessionLocal", AsyncSessionLocalTest)
    monkeypatch.setattr("satya.core.audit_logger.AsyncSessionLocal", AsyncSessionLocalTest)

    audit_logger = AuditLogger()
    app.state.audit_logger = audit_logger
    await audit_logger.start()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["X-Satya-Key"] = os.environ["SATYA_AGENT_KEYS"].split(",")[0]
        yield ac

    await audit_logger.stop()
    app.dependency_overrides.clear()
