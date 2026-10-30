import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class TimeslotWatch(Base):
    __tablename__ = "timeslot_watches"
    __table_args__ = (
        CheckConstraint("status IN ('active','done','failed','cancelled')"),
        CheckConstraint("date_from <= date_to"),
        Index("ix_timeslot_watches_one_active", "shop_id", "invoice_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    shop_id: Mapped[str] = mapped_column(String(32))
    invoice_id: Mapped[int] = mapped_column(BigInteger)
    invoice_number: Mapped[str] = mapped_column(String(64))
    stock_title: Mapped[str] = mapped_column(String(200), default="")
    date_from: Mapped[date]
    date_to: Mapped[date]
    status: Mapped[str] = mapped_column(String(16), default="active")
    message: Mapped[str] = mapped_column(String(300), default="")
    checks: Mapped[int] = mapped_column(default=0)
    current_time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
