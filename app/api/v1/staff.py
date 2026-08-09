import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.core.deps import require
from app.core.permissions import Permission
from app.core.security import hash_secret_async, verify_secret_async
from app.core.tenancy import TenantContext
from app.models.user import User, UserRole
from app.schemas.user import StaffCreateRequest, StaffResponse, StaffUpdateRequest
from app.services import audit_service

router = APIRouter(prefix="/staff", tags=["staff"])

_ROLE_LABELS = {UserRole.owner: "เจ้าของร้าน", UserRole.manager: "ผู้จัดการ", UserRole.cashier: "แคชเชียร์"}


async def _reject_duplicate_pin(ctx: TenantContext, pin: str, exclude_user_id: uuid.UUID | None = None) -> None:
    """PIN login identifies a person by their PIN alone within the shop (see
    auth.pin_login), so two staff sharing one means whoever the scan reaches
    first wins — a cashier could land in the owner's session. Hashes are
    salted, so the only way to compare is to verify the new PIN against each
    existing one; that's fine on this rarely-called admin path."""
    query = ctx.scoped(User).where(User.pin_code_hash.is_not(None))
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    for existing in await ctx.db.scalars(query):
        if await verify_secret_async(pin, existing.pin_code_hash):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"PIN นี้ถูกใช้โดย {existing.name} แล้ว กรุณาตั้ง PIN อื่น",
            )


@router.get("", response_model=list[StaffResponse])
async def list_staff(ctx: TenantContext = Depends(require(Permission.manage_staff))) -> list[User]:
    result = await ctx.db.scalars(ctx.scoped(User))
    return list(result)


@router.post("", response_model=StaffResponse, status_code=201)
async def create_staff(
    body: StaffCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_staff)),
) -> User:
    if body.pin:
        await _reject_duplicate_pin(ctx, body.pin)

    user = User(
        tenant_id=ctx.tenant_id,
        name=body.name,
        role=body.role,
        pin_code_hash=await hash_secret_async(body.pin) if body.pin else None,
    )
    ctx.db.add(user)
    await audit_service.record(
        ctx, "staff.create", f"เพิ่มพนักงานใหม่ {user.name} ({_ROLE_LABELS[user.role]})"
    )
    await ctx.db.commit()
    await ctx.db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=StaffResponse)
async def update_staff(
    user_id: uuid.UUID,
    body: StaffUpdateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_staff)),
) -> User:
    user = await ctx.db.scalar(ctx.scoped(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "staff member not found")

    # A locked-out shop has no recovery path, so both ways of losing the last
    # active owner are blocked here rather than left to the client to avoid.
    if user_id == ctx.user_id and body.is_active is False:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot deactivate your own account")

    will_lose_owner = user.role == UserRole.owner and user.is_active and (
        (body.role is not None and body.role != UserRole.owner) or body.is_active is False
    )
    if will_lose_owner:
        other_active_owners = await ctx.db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == ctx.tenant_id,
                User.role == UserRole.owner,
                User.is_active.is_(True),
                User.id != user_id,
            )
        )
        if not other_active_owners:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "cannot remove the last active owner of the shop"
            )

    # Checked up here with the other validations, before anything on `user`
    # is touched — the lookup it does would otherwise autoflush half-applied
    # changes into the transaction on its way to raising.
    if body.pin is not None:
        await _reject_duplicate_pin(ctx, body.pin, exclude_user_id=user.id)

    changes = []
    if body.role is not None:
        changes.append(f"เปลี่ยนบทบาทเป็น{_ROLE_LABELS[body.role]}")
        user.role = body.role
    if body.is_active is not None:
        changes.append("เปิดใช้งาน" if body.is_active else "ปิดใช้งาน")
        user.is_active = body.is_active
    if body.pin is not None:
        changes.append("รีเซ็ต PIN")
        user.pin_code_hash = await hash_secret_async(body.pin)

    if changes:
        await audit_service.record(
            ctx, "staff.update", f"แก้ไขพนักงาน {user.name}: {', '.join(changes)}"
        )
    await ctx.db.commit()
    await ctx.db.refresh(user)
    return user
