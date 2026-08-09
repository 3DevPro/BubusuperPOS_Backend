from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_token_payload
from app.core.rate_limit import FailureLimiter, login_limiter, pin_login_limiter
from app.core.security import (
    TokenPayload,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_secret_async,
    verify_secret_async,
)
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    PinLoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id, user.tenant_id, user.role.value, branch_id=user.branch_id),
        refresh_token=create_refresh_token(user.id, user.tenant_id, user.role.value, branch_id=user.branch_id),
    )


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenResponse:
    tenant = Tenant(name=body.business_name)
    db.add(tenant)
    await db.flush()

    owner = User(
        tenant_id=tenant.id,
        name=body.owner_name,
        email=body.email,
        password_hash=await hash_secret_async(body.password),
        role=UserRole.owner,
    )
    db.add(owner)
    try:
        await db.commit()
    except IntegrityError as exc:
        # No SELECT-then-INSERT pre-check: two signups for the same email
        # arriving together would both pass it and the loser would surface
        # the unique constraint as a 500. Same idiom as customer_service —
        # the constraint is the one source of truth about what's taken.
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from exc

    return _issue_tokens(owner)


def _enforce_limit(limiter: FailureLimiter, key: str) -> None:
    retry_after = limiter.retry_after(key)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "พยายามเข้าสู่ระบบผิดหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenResponse:
    # Keyed on the email being attacked rather than the caller's address: the
    # source IP arrives through Caddy and is only as trustworthy as the
    # forwarding headers, while the account under attack is right here in the
    # request and can't be spoofed away.
    limit_key = body.email.lower()
    _enforce_limit(login_limiter, limit_key)

    user = await db.scalar(select(User).where(User.email == body.email))
    if (
        user is None
        or user.password_hash is None
        or not await verify_secret_async(body.password, user.password_hash)
    ):
        login_limiter.record_failure(limit_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account is disabled")

    login_limiter.clear(limit_key)
    return _issue_tokens(user)


@router.post("/pin-login", response_model=TokenResponse)
async def pin_login(body: PinLoginRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenResponse:
    # A PIN is 4-6 digits, so this endpoint is the softest target in the app —
    # the cap per shop is what keeps it from being walked through end to end.
    limit_key = str(body.tenant_id)
    _enforce_limit(pin_login_limiter, limit_key)

    # PIN is only unique within a tenant (device stays scoped to one shop), so we
    # scan that tenant's active PIN-holders rather than looking PIN up directly.
    candidates = await db.scalars(
        select(User).where(
            User.tenant_id == body.tenant_id,
            User.is_active.is_(True),
            User.pin_code_hash.is_not(None),
        )
    )
    for user in candidates:
        if await verify_secret_async(body.pin, user.pin_code_hash):
            pin_login_limiter.clear(limit_key)
            return _issue_tokens(user)

    pin_login_limiter.record_failure(limit_key)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid PIN")


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if payload.type != TokenType.refresh:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a refresh token is required")

    user = await db.get(User, payload.sub)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account no longer available")

    return _issue_tokens(user)


@router.get("/me", response_model=MeResponse)
async def me(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MeResponse:
    # Deliberately not CurrentTenant — that rejects branch_champion tokens
    # (no tenant_id), and "who am I" must work for any authenticated
    # principal, shop or branch alike.
    user = await db.get(User, payload.sub)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return MeResponse(
        id=user.id, tenant_id=user.tenant_id, branch_id=user.branch_id, name=user.name, role=user.role.value
    )
