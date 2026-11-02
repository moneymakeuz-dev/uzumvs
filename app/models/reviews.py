import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base

METHODS_CHECK = "IN ('ai','template','skip')"


class ReviewSettings(Base):
    """A'zoning sharhlarga avtomatik javob sozlamasi; `since` dan oldingi sharhlarga avtomatik javob yozilmaydi."""

    __tablename__ = "review_settings"
    __table_args__ = (
        CheckConstraint(f"text_method {METHODS_CHECK}"),
        CheckConstraint(f"empty_method {METHODS_CHECK}"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_reply: Mapped[bool] = mapped_column(default=False)
    text_method: Mapped[str] = mapped_column(String(16), default="ai")
    empty_method: Mapped[str] = mapped_column(String(16), default="template")
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewShopSettings(Base):
    """Do'kon uchun alohida usul; NULL — shu turdagi sharhlarga umumiy sozlama ishlatiladi."""

    __tablename__ = "review_shop_settings"
    __table_args__ = (
        CheckConstraint(f"text_method {METHODS_CHECK}"),
        CheckConstraint(f"empty_method {METHODS_CHECK}"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    text_method: Mapped[str | None] = mapped_column(String(16))
    empty_method: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewTemplate(Base):
    __tablename__ = "review_templates"
    __table_args__ = (CheckConstraint("rating_min BETWEEN 1 AND 5 AND rating_max BETWEEN 1 AND 5 AND rating_min <= rating_max"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    rating_min: Mapped[int] = mapped_column(SmallInteger)
    rating_max: Mapped[int] = mapped_column(SmallInteger)
    text_uz: Mapped[str] = mapped_column(String(500))
    text_ru: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewReply(Base):
    """Bitta sharhga javob: loyiha (draft), yuborilmoqda, yuborilgan yoki xato. Mijoz ismi va sharh matni saqlanmaydi."""

    __tablename__ = "review_replies"
    __table_args__ = (
        UniqueConstraint("user_id", "review_id"),
        CheckConstraint("status IN ('draft','sending','sent','failed')"),
        CheckConstraint("method IN ('ai','template','manual')"),
        CheckConstraint("source IN ('auto','manual')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    review_id: Mapped[int] = mapped_column(BigInteger)
    shop_title: Mapped[str] = mapped_column(String(200), default="")
    product_title: Mapped[str] = mapped_column(String(300), default="")
    rating: Mapped[int] = mapped_column(SmallInteger)
    language: Mapped[str] = mapped_column(String(2))
    method: Mapped[str] = mapped_column(String(16))
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_templates.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    source: Mapped[str] = mapped_column(String(8), default="manual")
    moderation: Mapped[str] = mapped_column(String(16), default="")
    error: Mapped[str] = mapped_column(String(300), default="")
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
