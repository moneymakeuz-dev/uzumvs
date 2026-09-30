import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')"),
        CheckConstraint("quota_state IN ('reserved','consumed','released')"),
        CheckConstraint("operation IN ('initial','regenerate_all','regenerate_field')"),
        Index("ix_jobs_queue", "status", "created_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
        Index("ix_jobs_one_active_per_user", "user_id", unique=True,
              postgresql_where=text("status IN ('queued','running')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    field_path: Mapped[str | None] = mapped_column(String(80))
    base_version: Mapped[int]
    input_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(80))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    provider_model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    rules_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_period: Mapped[str] = mapped_column(String(7))
    quota_bucket: Mapped[str] = mapped_column(String(16))
    quota_state: Mapped[str] = mapped_column(String(16), default="reserved")


class AICallUsage(Base):
    __tablename__ = "ai_call_usage"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"))
    attempt_number: Mapped[int]
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    thinking_tokens: Mapped[int | None]
    budget_day: Mapped[str] = mapped_column(String(10))
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    budget_state: Mapped[str] = mapped_column(String(16), default="reserved")
    cost_basis: Mapped[str] = mapped_column(String(16), default="upper_bound")
    price_version: Mapped[str] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(30), default="unknown")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
