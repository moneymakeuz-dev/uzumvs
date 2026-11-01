import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.db import Base


class UzumAccount(Base):
    """A'zoning Uzum ulanishi: *_secret ustunlari UZUM_CREDENTIALS_KEY bilan Fernet-shifrlangan."""

    __tablename__ = "uzum_accounts"
    __table_args__ = (CheckConstraint("status IN ('active','token_failed','login_failed')"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    login_hint: Mapped[str] = mapped_column(String(80))
    login_secret: Mapped[str] = mapped_column(Text)
    password_secret: Mapped[str] = mapped_column(Text)
    api_token_secret: Mapped[str] = mapped_column(Text)
    access_token_secret: Mapped[str | None] = mapped_column(Text)
    shops_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    shops_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="active")
    status_message: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
