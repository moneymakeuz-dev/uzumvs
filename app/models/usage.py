import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class MonthlyUsage(Base):
    __tablename__ = "monthly_usage"
    __table_args__ = (
        CheckConstraint("initial_used >= 0 AND initial_reserved >= 0 AND regeneration_used >= 0 AND regeneration_reserved >= 0"),
        CheckConstraint("initial_used + initial_reserved <= initial_limit"),
        CheckConstraint("regeneration_used + regeneration_reserved <= regeneration_limit"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    initial_limit: Mapped[int] = mapped_column(default=20)
    initial_used: Mapped[int] = mapped_column(default=0)
    initial_reserved: Mapped[int] = mapped_column(default=0)
    regeneration_limit: Mapped[int] = mapped_column(default=40)
    regeneration_used: Mapped[int] = mapped_column(default=0)
    regeneration_reserved: Mapped[int] = mapped_column(default=0)


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (CheckConstraint("accepted_jobs >= 0 AND created_drafts >= 0"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    accepted_jobs: Mapped[int] = mapped_column(default=0)
    created_drafts: Mapped[int] = mapped_column(default=0)


class DailyBudget(Base):
    __tablename__ = "ai_daily_budget"
    __table_args__ = (CheckConstraint("spent_usd >= 0 AND reserved_usd >= 0 AND limit_usd >= 0"),)

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal("0"))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal("0"))


class DeletionEvent(Base):
    __tablename__ = "deletion_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[uuid.UUID]
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
