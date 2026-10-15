import asyncio

import pytest

from satya.core.audit_logger import AuditLogger
from satya.schemas.audit import AuditEventSchema


def make_event(event_id):
    return AuditEventSchema(
        event_id=event_id,
        timestamp="2026-10-15T00:00:00Z",
        agent_id="lifecycle-test-agent",
        decision="ALLOW",
        payload_hash=f"hash-{event_id}",
        latency_ms=1.0,
    )


@pytest.mark.asyncio
async def test_stop_drains_all_accepted_audit_events(monkeypatch):
    logger = AuditLogger()
    saved = []

    async def fake_write(event):
        await asyncio.sleep(0.005)
        saved.append(event.event_id)

    monkeypatch.setattr(logger, "_write_to_db", fake_write)
    await logger.start()
    for index in range(25):
        await logger.log(make_event(f"drain-{index}"))

    await asyncio.wait_for(logger.stop(), timeout=5)

    assert sorted(saved) == sorted(f"drain-{index}" for index in range(25))
    assert logger.failed_writes == 0
    assert logger.last_error is None


@pytest.mark.asyncio
async def test_audit_worker_retries_transient_db_error(monkeypatch):
    logger = AuditLogger()
    saved = []
    attempts = 0

    async def flaky_write(event):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise RuntimeError("temporary DB outage")
        saved.append(event.event_id)

    monkeypatch.setattr(logger, "_write_to_db", flaky_write)
    await logger.start()
    await logger.log(make_event("retry-event"))
    await asyncio.wait_for(logger.stop(), timeout=5)

    assert attempts == 2
    assert saved == ["retry-event"]
    assert logger.failed_writes == 0
    assert logger.last_error is None
