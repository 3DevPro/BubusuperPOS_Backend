import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class UserRole(str, enum.Enum):
    owner = "owner"
    manager = "manager"
    cashier = "cashier"
    # A Ngernturbo branch employee, not a shop's own staff — see
    # app/core/branch_scope.py. Such a user has no tenant_id (they don't
    # belong to any one shop) and a branch_id instead, so User can't use
    # TenantScopedMixin's non-nullable tenant_id like every other tenant-
    # owned table does; both FKs below are nullable and exactly one is set,
    # depending on the role.
    branch_champion = "branch_champion"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null only for branch_champion users (see UserRole.branch_champion) —
    # every other role always has one, enforced at the application layer
    # since the DB can't express "nullable depending on role".
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("turbo_branches.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    # Globally unique (not just per-tenant) so email+password login can find the
    # account without asking which tenant first. Cashiers created for PIN-only
    # login can leave this null.
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    pin_code_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(default=UserRole.cashier)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
