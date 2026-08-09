import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base
from app.models.base import TenantScopedMixin


class AuditLog(TenantScopedMixin, Base):
    """Append-only ledger of administrative actions (staff/catalog/settings changes).
    Rows are written in the same transaction as the action they describe — see
    app/services/audit_service.py — so a failed action never leaves a stray row."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
