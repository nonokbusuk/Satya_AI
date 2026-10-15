import asyncio
import logging
from typing import AsyncIterator, List

from ..schemas.audit import AuditEventSchema, AuditQueryFilters, AuditSummary
from ..database import AsyncSessionLocal
from ..models.audit_event import AuditEvent

logger = logging.getLogger(__name__)


class AuditLogger:
    """Queued audit writer with graceful draining, retries, and visible failures."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.worker_task = None
        self.failed_writes = 0
        self.last_error = None
        self._stopping = False

    async def start(self):
        if self.worker_task is None or self.worker_task.done():
            self._stopping = False
            self.worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        """Drain accepted audit events before stopping the worker."""
        if self.worker_task and not self.worker_task.done():
            self._stopping = True
            await self.queue.join()
            await self.queue.put(None)
            await self.worker_task
        self.worker_task = None

    async def _worker(self):
        while True:
            event = await self.queue.get()
            try:
                if event is None:
                    return
                saved = False
                last_exception = None
                for attempt in range(3):
                    try:
                        await self._write_to_db(event)
                        saved = True
                        self.last_error = None
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        last_exception = exc
                        logger.exception(
                            "Audit write failed (attempt %s/3) for event %s",
                            attempt + 1,
                            getattr(event, "event_id", "unknown"),
                        )
                        if attempt < 2:
                            await asyncio.sleep(0.1 * (attempt + 1))
                if not saved:
                    self.failed_writes += 1
                    self.last_error = str(last_exception or "unknown audit write failure")
                    logger.error(
                        "Audit event %s failed after retries; failed_writes=%s",
                        getattr(event, "event_id", "unknown"),
                        self.failed_writes,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected audit worker failure")
                self.failed_writes += 1
                self.last_error = "Unexpected audit worker failure"
            finally:
                self.queue.task_done()

    async def _write_to_db(self, event: AuditEventSchema):
        async with AsyncSessionLocal() as session:
            try:
                db_event = AuditEvent(
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                    agent_id=event.agent_id,
                    decision=event.decision,
                    matched_policies=[p for p in (event.matched_policies or [])],
                    violations=[v.model_dump() for v in (event.violations or [])],
                    payload_hash=event.payload_hash,
                    context_metadata=event.context_metadata,
                    latency_ms=event.latency_ms,
                )
                session.add(db_event)
                await session.commit()
            except Exception:
                logger.exception("DB insertion failed for audit event %s", event.event_id)
                await session.rollback()
                raise

    async def log(self, event: AuditEventSchema) -> None:
        if self._stopping:
            raise RuntimeError("Audit logger is shutting down; refusing new audit events.")
        if not self.worker_task or self.worker_task.done():
            await self.start()
        await self.queue.put(event)

    async def query(self, filters: AuditQueryFilters) -> List[AuditEventSchema]:
        from sqlalchemy import select, desc

        async with AsyncSessionLocal() as session:
            query = select(AuditEvent).order_by(desc(AuditEvent.timestamp))
            if filters.agent_id:
                query = query.where(AuditEvent.agent_id == filters.agent_id)
            if filters.decision:
                query = query.where(AuditEvent.decision == filters.decision)
            query = query.limit(filters.limit)
            if filters.cursor:
                query = query.offset(filters.cursor)
            result = await session.execute(query)
            db_events = result.scalars().all()
            return [
                AuditEventSchema(
                    event_id=e.event_id,
                    timestamp=e.timestamp,
                    agent_id=e.agent_id,
                    decision=e.decision,
                    matched_policies=e.matched_policies,
                    violations=e.violations,
                    payload_hash=e.payload_hash,
                    context_metadata=e.context_metadata,
                    latency_ms=e.latency_ms,
                )
                for e in db_events
            ]

    async def export_ndjson(self, filters: AuditQueryFilters) -> AsyncIterator[str]:
        events = await self.query(filters)
        for event in events:
            yield event.model_dump_json() + "\n"

    async def get_summary(self) -> AuditSummary:
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditEvent))
            events = result.scalars().all()

            decisions = {}
            agent_counts = {}
            for event in events:
                decisions[event.decision] = decisions.get(event.decision, 0) + 1
                if event.decision == "DENY":
                    agent_counts[event.agent_id] = agent_counts.get(event.agent_id, 0) + 1

            top_violating = [
                {"agent_id": key, "violations": value}
                for key, value in sorted(
                    agent_counts.items(), key=lambda item: item[1], reverse=True
                )[:5]
            ]
            return AuditSummary(
                total_events=len(events),
                decisions_breakdown=decisions,
                top_violating_agents=top_violating,
            )
