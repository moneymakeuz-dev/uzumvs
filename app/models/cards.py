import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("user_id", "creation_key"),
        CheckConstraint("status IN ('draft', 'ready')"),
        CheckConstraint("version >= 1"),
        Index("ix_cards_owner_history", "user_id", "deleted_at", "updated_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    creation_key: Mapped[str] = mapped_column(String(80))
    creation_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(default=1)
    seller_notes: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_ai_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review_resolutions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    schema_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CardImage(Base):
    __tablename__ = "card_images"
    __table_args__ = (UniqueConstraint("card_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(128), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(30), default="image/jpeg")
    width: Mapped[int]
    height: Mapped[int]
    size_bytes: Mapped[int]
    position: Mapped[int]
